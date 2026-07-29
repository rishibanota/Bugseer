"""Dependency graph construction and Phase 5 impact analysis.

Two independent signals are combined:

  1. **Static imports** - resolved from each file's import statements to actual
     files in the repository. Directed edge: importer -> imported.
  2. **Historical co-change** - files that are habitually committed together,
     which captures coupling that imports miss (a template and its view, a
     schema and its migration, a config key and its consumer).

The "what if I change X?" question is answered by propagating outward from the
seed files through reverse-import edges (things that would break if X changes)
and co-change edges (things that historically had to change too), with a decay
per hop so distant nodes rank below immediate ones.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import networkx as nx

from bugseer.models import ImpactResult


# --------------------------------------------------------------------------
# Import resolution
# --------------------------------------------------------------------------
_PY_EXT = (".py", ".pyi")
_JS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def _candidates_for_python(module: str, importer: str, all_files: set[str]) -> list[str]:
    """Resolve a Python module path (absolute or relative) to repository files."""
    out: list[str] = []
    if module.startswith("."):
        level = len(module) - len(module.lstrip("."))
        rest = module.lstrip(".")
        base = Path(importer).parent
        for _ in range(max(0, level - 1)):
            base = base.parent
        target = base / rest.replace(".", "/") if rest else base
        stems = [str(target), str(target / "__init__")]
    else:
        parts = module.split(".")
        stems = []
        # Try progressively shorter prefixes: `a.b.c` may be file a/b.py with
        # attribute c, or package a/b/c/__init__.py.
        for i in range(len(parts), 0, -1):
            prefix = "/".join(parts[:i])
            stems.append(prefix)
            stems.append(f"{prefix}/__init__")
            stems.append(f"src/{prefix}")
            stems.append(f"src/{prefix}/__init__")

    for stem in stems:
        stem = stem.replace("\\", "/").lstrip("./")
        for ext in _PY_EXT:
            cand = f"{stem}{ext}"
            if cand in all_files:
                out.append(cand)
        if out:
            break
    return out


def _candidates_for_js(spec: str, importer: str, all_files: set[str]) -> list[str]:
    """Resolve a JS/TS import specifier to repository files."""
    if not spec.startswith((".", "/", "~", "@/", "src/")):
        return []  # bare specifier => node_modules, not our code
    spec_clean = spec.lstrip("~")
    if spec_clean.startswith("@/"):
        spec_clean = "src/" + spec_clean[2:]

    if spec_clean.startswith("."):
        base = (Path(importer).parent / spec_clean).as_posix()
        base = os.path.normpath(base).replace("\\", "/")
    else:
        base = spec_clean.lstrip("/")

    stems = [base, f"{base}/index"]
    out: list[str] = []
    for stem in stems:
        if stem in all_files:
            out.append(stem)
        for ext in _JS_EXT:
            cand = f"{stem}{ext}"
            if cand in all_files:
                out.append(cand)
        if out:
            break
    return out


def _candidates_generic(spec: str, importer: str, all_files: set[str],
                        by_stem: dict[str, list[str]]) -> list[str]:
    """Last-resort resolution: match on file stem (Go packages, Java classes, C headers)."""
    token = re.split(r"[./:\\]", spec.strip().strip("\"'<>"))[-1]
    if not token or len(token) < 3:
        return []
    matches = by_stem.get(token.lower(), [])
    if len(matches) == 1:
        return matches
    # Prefer a match in the same directory when the stem is ambiguous.
    importer_dir = str(Path(importer).parent)
    same_dir = [m for m in matches if str(Path(m).parent) == importer_dir]
    if len(same_dir) == 1:
        return same_dir
    return []


def build_dependency_graph(
    metrics_by_path: dict[str, Any],
    cochange: dict[str, list[tuple[str, int]]] | None = None,
) -> nx.DiGraph:
    """Build a directed import graph, annotated with co-change weights."""
    all_files = set(metrics_by_path)
    by_stem: dict[str, list[str]] = defaultdict(list)
    for path in all_files:
        by_stem[Path(path).stem.lower()].append(path)

    graph = nx.DiGraph()
    graph.add_nodes_from(all_files)

    for path, metrics in metrics_by_path.items():
        language = getattr(metrics, "language", "unknown")
        imports = getattr(metrics, "imports", []) or []
        resolved: set[str] = set()
        for spec in imports:
            if not spec:
                continue
            targets: list[str] = []
            if language == "python":
                targets = _candidates_for_python(spec, path, all_files)
            elif language in ("javascript", "typescript", "tsx", "jsx"):
                targets = _candidates_for_js(spec, path, all_files)
            if not targets:
                targets = _candidates_generic(spec, path, all_files, by_stem)
            for target in targets:
                if target != path:
                    resolved.add(target)

        for target in resolved:
            graph.add_edge(path, target, kind="import", weight=1.0)

        if hasattr(metrics, "internal_deps"):
            metrics.internal_deps = sorted(resolved)

    # Overlay co-change coupling as a separate, undirected-ish signal.
    if cochange:
        for path, partners in cochange.items():
            if path not in graph:
                continue
            for partner, count in partners:
                if partner not in graph or partner == path:
                    continue
                if graph.has_edge(path, partner):
                    graph[path][partner]["cochange"] = count
                else:
                    graph.add_edge(path, partner, kind="cochange", weight=0.5, cochange=count)

    return graph


def compute_centrality(graph: nx.DiGraph, max_nodes: int = 3000) -> dict[str, float]:
    """Betweenness centrality, approximated on large graphs to stay fast."""
    if graph.number_of_nodes() == 0:
        return {}
    import_graph = nx.DiGraph()
    import_graph.add_nodes_from(graph.nodes())
    for u, v, data in graph.edges(data=True):
        if data.get("kind") == "import":
            import_graph.add_edge(u, v)
    if import_graph.number_of_edges() == 0:
        return {node: 0.0 for node in graph.nodes()}
    try:
        k = min(import_graph.number_of_nodes(), 128) if import_graph.number_of_nodes() > max_nodes else None
        centrality = nx.betweenness_centrality(import_graph, k=k, normalized=True)
    except Exception:  # noqa: BLE001
        centrality = {node: 0.0 for node in graph.nodes()}
    return centrality


# --------------------------------------------------------------------------
# Phase 5: "What if?" impact simulator
# --------------------------------------------------------------------------
def simulate_impact(
    graph: nx.DiGraph,
    seeds: Iterable[str],
    *,
    risk_by_path: dict[str, float] | None = None,
    cochange_strength: dict[str, dict[str, float]] | None = None,
    max_hops: int = 3,
    limit: int = 25,
    decay: float = 0.45,
) -> ImpactResult:
    """Predict which files are most likely to be affected by changing `seeds`.

    Scoring per candidate file:
        import_pressure  - reachability through reverse-import edges, decayed by hop
        cochange_pressure- historical co-commit strength with any seed
        risk_weight      - the candidate's own bug-risk score (fragile files break first)
    """
    seed_list = [s for s in seeds if s in graph]
    missing = [s for s in seeds if s not in graph]
    risk_by_path = risk_by_path or {}
    cochange_strength = cochange_strength or {}

    if not seed_list:
        return ImpactResult(
            seeds=list(seeds),
            affected=[],
            explanation=[
                f"None of the requested files are in the dependency graph: {', '.join(missing)}"
                if missing else "No seed files supplied."
            ],
        )

    scores: dict[str, float] = defaultdict(float)
    reasons: dict[str, list[str]] = defaultdict(list)
    hop_of: dict[str, int] = {}

    # --- 1. reverse-import propagation (who breaks if the seed changes) ----
    reverse = graph.reverse(copy=False)
    for seed in seed_list:
        frontier = {seed}
        visited = {seed}
        for hop in range(1, max_hops + 1):
            next_frontier: set[str] = set()
            for node in frontier:
                for dependent in reverse.successors(node):
                    edge = graph.get_edge_data(dependent, node, default={})
                    if edge.get("kind") != "import":
                        continue
                    if dependent in visited:
                        continue
                    next_frontier.add(dependent)
                    visited.add(dependent)
                    weight = (decay ** (hop - 1)) * 10.0
                    scores[dependent] += weight
                    hop_of[dependent] = min(hop_of.get(dependent, 99), hop)
                    if hop == 1:
                        reasons[dependent].append(f"directly imports `{node}`")
                    else:
                        reasons[dependent].append(
                            f"depends on `{node}` transitively ({hop} hops from `{seed}`)"
                        )
            frontier = next_frontier
            if not frontier:
                break

    # --- 2. historical co-change ------------------------------------------
    for seed in seed_list:
        partners = cochange_strength.get(seed, {})
        for partner, strength in partners.items():
            if partner in seed_list or partner not in graph:
                continue
            scores[partner] += min(12.0, strength * 14.0)
            hop_of.setdefault(partner, 1)
            reasons[partner].append(
                f"changed together with `{seed}` in {strength:.0%} of its commits"
            )

    # --- 3. weight by the candidate's own fragility ------------------------
    affected: list[dict[str, Any]] = []
    for path, base in scores.items():
        if path in seed_list:
            continue
        own_risk = risk_by_path.get(path, 0.0)
        total = base * (1.0 + own_risk / 120.0)
        if own_risk >= 60:
            reasons[path].append(f"is itself high-risk ({own_risk:.0f}/100)")
        affected.append({
            "path": path,
            "impact_score": round(min(100.0, total * 3.2), 1),
            "hops": hop_of.get(path, 1),
            "own_risk": round(own_risk, 1),
            "reasons": reasons[path][:4],
        })

    affected.sort(key=lambda a: a["impact_score"], reverse=True)
    affected = affected[:limit]

    explanation = [
        f"Analysed {len(seed_list)} seed file(s) across up to {max_hops} dependency hops.",
        f"{len(scores)} file(s) show some coupling; showing the top {len(affected)}.",
    ]
    if missing:
        explanation.append(f"Not found in graph (ignored): {', '.join(missing)}")
    direct = sum(1 for a in affected if a["hops"] == 1)
    if direct:
        explanation.append(f"{direct} of them are directly coupled (1 hop or co-change).")

    return ImpactResult(seeds=seed_list, affected=affected, explanation=explanation)


def graph_payload(graph: nx.DiGraph, risk_by_path: dict[str, float],
                  limit: int = 400) -> dict[str, Any]:
    """Serialize the graph for the frontend visualisation, trimmed to `limit` nodes."""
    degrees = {n: graph.in_degree(n) + graph.out_degree(n) for n in graph.nodes()}
    ranked = sorted(
        graph.nodes(),
        key=lambda n: (risk_by_path.get(n, 0.0) * 2 + degrees.get(n, 0)),
        reverse=True,
    )[:limit]
    keep = set(ranked)
    nodes = [
        {
            "id": n,
            "risk": round(risk_by_path.get(n, 0.0), 1),
            "in": graph.in_degree(n),
            "out": graph.out_degree(n),
        }
        for n in ranked
    ]
    edges = [
        {"source": u, "target": v, "kind": d.get("kind", "import")}
        for u, v, d in graph.edges(data=True)
        if u in keep and v in keep
    ]
    return {"nodes": nodes, "edges": edges, "truncated": graph.number_of_nodes() > limit}
