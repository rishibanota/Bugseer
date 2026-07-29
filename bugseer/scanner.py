"""The orchestrator: walks the repo and runs every phase to produce a RepoReport."""

from __future__ import annotations

import fnmatch
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from bugseer import __version__
from bugseer.analysis.duplication import CrossFileDuplication
from bugseer.analysis.static import analyze_file
from bugseer.config import Config
from bugseer.git_intel import (
    GitAnalyzer,
    find_coverage_file,
    infer_test_coverage_by_convention,
    is_git_repo,
    parse_coverage,
)
from bugseer.graph import build_dependency_graph, compute_centrality, graph_payload
from bugseer.ml import BugPredictor, build_feature_vector
from bugseer.models import FileMetrics, FileRisk, GitMetrics, RepoReport
from bugseer.rules import band_for, evaluate_git, evaluate_graph, evaluate_static, squash


# --------------------------------------------------------------------------
# File discovery
# --------------------------------------------------------------------------
def _matches_any(rel_path: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        # Directory-prefix patterns like "node_modules/*" must match at any depth.
        if pattern.endswith("/*"):
            prefix = pattern[:-2]
            if rel_path == prefix or f"/{prefix}/" in f"/{rel_path}" or rel_path.startswith(f"{prefix}/"):
                return True
    return False


def discover_files(cfg: Config) -> list[tuple[Path, str, str]]:
    """Return [(absolute_path, relative_path, language)] for every scannable file."""
    root = cfg.root
    out: list[tuple[Path, str, str]] = []
    skip_dirs = {
        ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
        "env", "dist", "build", "out", "target", ".next", ".nuxt", ".tox",
        ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "vendor",
        "htmlcov", "coverage", ".bugseer", ".idea", ".vscode", ".gradle",
    }

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".egg")]
        for filename in filenames:
            abs_path = Path(dirpath) / filename
            try:
                rel_path = str(abs_path.relative_to(root)).replace("\\", "/")
            except ValueError:
                continue

            language = cfg.extensions.get(abs_path.suffix.lower())
            if not language:
                continue
            if _matches_any(rel_path, cfg.exclude):
                continue
            if cfg.include and not _matches_any(rel_path, cfg.include):
                continue
            if cfg.ignore_tests and cfg.is_test_path(rel_path):
                continue
            out.append((abs_path, rel_path, language))

    out.sort(key=lambda item: item[1])
    return out


def _analyze_one(args: tuple[str, str, str, int]) -> tuple[str, FileMetrics, dict]:
    abs_path, rel_path, language, max_bytes = args
    metrics, fps = analyze_file(Path(abs_path), rel_path, language, max_bytes)
    return rel_path, metrics, fps


# --------------------------------------------------------------------------
# Scanner
# --------------------------------------------------------------------------
class Scanner:
    def __init__(self, cfg: Config, progress: Callable[[str, int, int], None] | None = None) -> None:
        self.cfg = cfg
        self.progress = progress or (lambda stage, done, total: None)
        self.git_analyzer: GitAnalyzer | None = None
        self.graph = None
        self.predictor: BugPredictor | None = None

    # ------------------------------------------------------------------
    def scan(self, *, train: bool = False, use_model: bool = True) -> RepoReport:
        started = time.time()
        cfg = self.cfg
        files = discover_files(cfg)
        total = len(files)

        # ---- Phase 1: static analysis -----------------------------------
        self.progress("static", 0, total)
        metrics_by_path: dict[str, FileMetrics] = {}
        dup_index = CrossFileDuplication()

        workers = cfg.workers or min(os.cpu_count() or 4, 8)
        payloads = [(str(a), r, lang, cfg.max_file_bytes) for a, r, lang in files]

        if workers > 1 and total > 24:
            try:
                with ProcessPoolExecutor(max_workers=workers) as pool:
                    futures = {pool.submit(_analyze_one, p): p[1] for p in payloads}
                    for done, future in enumerate(as_completed(futures), start=1):
                        rel_path, metrics, fps = future.result()
                        metrics_by_path[rel_path] = metrics
                        dup_index.add(rel_path, fps)
                        if done % 25 == 0 or done == total:
                            self.progress("static", done, total)
            except Exception:  # noqa: BLE001 - fall back to serial on any pool failure
                metrics_by_path.clear()
                dup_index = CrossFileDuplication()
                for done, payload in enumerate(payloads, start=1):
                    rel_path, metrics, fps = _analyze_one(payload)
                    metrics_by_path[rel_path] = metrics
                    dup_index.add(rel_path, fps)
                    self.progress("static", done, total)
        else:
            for done, payload in enumerate(payloads, start=1):
                rel_path, metrics, fps = _analyze_one(payload)
                metrics_by_path[rel_path] = metrics
                dup_index.add(rel_path, fps)
                self.progress("static", done, total)

        clones = dup_index.clones()

        # ---- Phase 2: git intelligence -----------------------------------
        git_metrics: dict[str, GitMetrics] = {}
        git_available = cfg.use_git and is_git_repo(cfg.root)
        git_summary: dict[str, Any] = {
            "available": False,
            "reason": (
                "git analysis disabled (--no-git)" if not cfg.use_git
                else "not a git repository"
            ),
        }
        if git_available:
            self.progress("git", 0, 1)
            self.git_analyzer = GitAnalyzer(
                cfg.root, history_days=cfg.history_days,
                extra_bugfix_pattern=cfg.bugfix_pattern,
            )
            git_metrics = self.git_analyzer.analyze()
            git_summary = self.git_analyzer.repo_summary()
            # A repo with unreadable history must not masquerade as "no repo":
            # Phase 2 rules are skipped, but the summary explains exactly why.
            git_available = bool(git_metrics)
            self.progress("git", 1, 1)

        # ---- coverage -----------------------------------------------------
        coverage_map: dict[str, float] = {}
        coverage_path = find_coverage_file(cfg.root, cfg.coverage_file)
        if coverage_path:
            coverage_map = parse_coverage(coverage_path, cfg.root)
        test_presence = infer_test_coverage_by_convention(
            list(metrics_by_path), cfg.is_test_path
        )

        # ---- dependency graph --------------------------------------------
        self.progress("graph", 0, 1)
        cochange_lists: dict[str, list[tuple[str, int]]] = {}
        if self.git_analyzer:
            for path in metrics_by_path:
                partners = self.git_analyzer.cochange_partners(path, limit=10)
                if partners:
                    cochange_lists[path] = partners
        self.graph = build_dependency_graph(metrics_by_path, cochange_lists)
        centrality = compute_centrality(self.graph)
        self.progress("graph", 1, 1)

        dependents_map: dict[str, list[str]] = {}
        dependencies_map: dict[str, list[str]] = {}
        for path in metrics_by_path:
            if path in self.graph:
                dependents_map[path] = sorted(
                    u for u, v, d in self.graph.in_edges(path, data=True)
                    if d.get("kind") == "import"
                )
                dependencies_map[path] = sorted(
                    v for u, v, d in self.graph.out_edges(path, data=True)
                    if d.get("kind") == "import"
                )
            else:
                dependents_map[path] = []
                dependencies_map[path] = []

        # ---- Phase 3: ML ---------------------------------------------------
        ml_used = False
        training_report = None
        if cfg.use_ml and git_available:
            self.predictor = BugPredictor(cfg.home_path)
            degrees = {
                p: (len(dependencies_map.get(p, [])), len(dependents_map.get(p, [])))
                for p in metrics_by_path
            }
            if train:
                self.progress("train", 0, 1)
                training_report = self.predictor.train(cfg.root, metrics_by_path, degrees)
                if training_report.trained:
                    self.predictor.save()
                self.progress("train", 1, 1)
            elif use_model:
                self.predictor.load()
            ml_used = self.predictor.available()

        # ---- assemble per-file risk ---------------------------------------
        self.progress("score", 0, total)
        results: list[FileRisk] = []
        for idx, (path, metrics) in enumerate(metrics_by_path.items(), start=1):
            gm = git_metrics.get(path) or GitMetrics(path=path)
            coverage = coverage_map.get(path)
            has_test = test_presence.get(path)

            static_hits = evaluate_static(
                metrics, cfg,
                coverage=coverage,
                has_test_file=has_test,
                cross_file_clones=clones.get(path),
            )
            git_hits = evaluate_git(gm, cfg) if git_available else []
            graph_hits = evaluate_graph(
                path, dependents_map.get(path, []), centrality.get(path, 0.0), cfg
            )

            hits = static_hits + git_hits + graph_hits
            static_score = sum(h.score for h in static_hits)
            git_score = sum(h.score for h in git_hits)
            graph_score = sum(h.score for h in graph_hits)

            ml_probability = None
            ml_contributions: list[dict] = []
            ml_points = 0.0
            if ml_used and self.predictor is not None:
                features = build_feature_vector(
                    metrics, gm,
                    len(dependencies_map.get(path, [])),
                    len(dependents_map.get(path, [])),
                )
                ml_probability = self.predictor.predict(features, path=path)
                if ml_probability is not None:
                    ml_contributions = self.predictor.explain(features)
                    ml_points = self.predictor.risk_points(ml_probability)
                    if ml_points > 1.0:
                        top = ml_contributions[0]["label"] if ml_contributions else "historical patterns"
                        oof = self.predictor.is_out_of_fold(path)
                        provenance = (
                            "cross-validated estimate (this file was in the training set, so "
                            "BugSeer reports what the model predicted while held out, not a "
                            "memorised value)"
                            if oof else
                            "prediction for a file the model was not trained on"
                        )
                        from bugseer.models import RuleHit
                        hits.append(RuleHit(
                            rule_id="ml-prediction",
                            title="Learned model predicts elevated defect risk",
                            score=ml_points,
                            detail=(
                                f"A model trained on this repository's own bug-fix history "
                                f"estimates a {ml_probability:.0%} probability that this file "
                                f"needs a fix within {self.predictor.report.label_window_days or 180} days "
                                f"(repo baseline {self.predictor.report.baseline_rate or 0:.0%}). "
                                f"Strongest signal: {top}. This is a {provenance}."
                            ),
                            phase="ml",
                            severity="high" if ml_points >= 15 else "medium",
                            evidence={
                                "probability": round(ml_probability, 4),
                                "baseline": self.predictor.report.baseline_rate,
                                "out_of_fold": oof,
                                "contributions": ml_contributions,
                            },
                        ))

            raw = static_score + git_score + graph_score + ml_points
            score = squash(raw)
            results.append(FileRisk(
                path=path,
                language=metrics.language,
                score=score,
                raw_score=raw,
                band=band_for(score, cfg),
                static_score=static_score,
                git_score=git_score,
                graph_score=graph_score,
                ml_probability=ml_probability,
                ml_contributions=ml_contributions,
                hits=sorted(hits, key=lambda h: h.score, reverse=True),
                metrics=metrics,
                git=gm if gm.commit_count else None,
                coverage=coverage,
                dependents=dependents_map.get(path, []),
                dependencies=dependencies_map.get(path, []),
                centrality=centrality.get(path, 0.0),
            ))
            if idx % 50 == 0 or idx == total:
                self.progress("score", idx, total)

        results.sort(key=lambda r: r.score, reverse=True)

        # ---- summary --------------------------------------------------------
        bands = {"low": 0, "medium": 0, "high": 0, "critical": 0}
        for r in results:
            bands[r.band] += 1
        languages: dict[str, int] = {}
        for r in results:
            languages[r.language] = languages.get(r.language, 0) + 1

        rule_counts: dict[str, int] = {}
        for r in results:
            for h in r.hits:
                rule_counts[h.rule_id] = rule_counts.get(h.rule_id, 0) + 1

        summary = {
            "files_scanned": len(results),
            "total_loc": sum(r.metrics.loc for r in results if r.metrics),
            "bands": bands,
            "languages": dict(sorted(languages.items(), key=lambda kv: -kv[1])),
            "average_score": round(sum(r.score for r in results) / max(1, len(results)), 2),
            "median_score": round(
                sorted(r.score for r in results)[len(results) // 2] if results else 0.0, 2
            ),
            "top_rules": [
                {"rule_id": k, "files": v}
                for k, v in sorted(rule_counts.items(), key=lambda kv: -kv[1])[:10]
            ],
            "git": git_summary,
            "coverage_source": str(coverage_path.name) if coverage_path else None,
            "parsers": self._parser_breakdown(metrics_by_path),
            "ml": (training_report.to_dict() if training_report
                   else (self.predictor.report.to_dict() if self.predictor and ml_used else None)),
            "coupled_developers": (
                self.git_analyzer.coupled_developers(5) if self.git_analyzer else []
            ),
            "graph": {
                "nodes": self.graph.number_of_nodes(),
                "import_edges": sum(
                    1 for _, _, d in self.graph.edges(data=True) if d.get("kind") == "import"
                ),
                "cochange_edges": sum(
                    1 for _, _, d in self.graph.edges(data=True) if d.get("kind") == "cochange"
                ),
            },
        }

        hotspots = [
            {
                "path": r.path,
                "score": r.score,
                "band": r.band,
                "reasons": [h.title for h in r.top_reasons(3)],
            }
            for r in results[:15]
        ]

        return RepoReport(
            root=str(cfg.root),
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            files=results,
            config=cfg.to_dict(),
            summary=summary,
            hotspots=hotspots,
            git_available=git_available,
            ml_used=ml_used,
            duration_seconds=time.time() - started,
            version=__version__,
        )

    @staticmethod
    def _parser_breakdown(metrics_by_path: dict[str, FileMetrics]) -> dict[str, int]:
        out: dict[str, int] = {}
        for m in metrics_by_path.values():
            out[m.parser] = out.get(m.parser, 0) + 1
        return out

    # ------------------------------------------------------------------
    def cochange_strength(self) -> dict[str, dict[str, float]]:
        """{path: {partner: strength}} for the impact simulator."""
        out: dict[str, dict[str, float]] = {}
        if not self.git_analyzer:
            return out
        for path, gm in self.git_analyzer._metrics.items():  # noqa: SLF001
            if gm.co_change_partners:
                out[path] = {
                    p["path"]: float(p["strength"]) for p in gm.co_change_partners
                }
        return out

    def graph_json(self, risk_by_path: dict[str, float]) -> dict[str, Any]:
        if self.graph is None:
            return {"nodes": [], "edges": []}
        return graph_payload(self.graph, risk_by_path)


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------
def save_report(report: RepoReport, path: Path, *, include_metrics: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.to_dict(include_metrics=include_metrics), indent=2),
        encoding="utf-8",
    )


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
