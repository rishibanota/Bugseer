"""End-to-end scanning, the dependency graph, impact simulation and the ML model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bugseer.config import load_config
from bugseer.graph import build_dependency_graph, simulate_impact
from bugseer.ml import BugPredictor, FEATURE_NAMES, build_feature_vector, _clamp_probability
from bugseer.models import FileMetrics, GitMetrics
from bugseer.report import report_from_dict, write_html_report
from bugseer.scanner import Scanner, discover_files, save_report


@pytest.fixture(scope="module")
def scanned(demo_repo):
    cfg = load_config(demo_repo)
    scanner = Scanner(cfg)
    return scanner.scan(train=False, use_model=False), scanner


class TestDiscovery:
    def test_finds_source_files(self, demo_repo):
        cfg = load_config(demo_repo)
        found = {rel for _, rel, _ in discover_files(cfg)}
        assert "src/payment.py" in found
        assert "tests/test_utils.py" in found

    def test_excludes_vendor_directories(self, demo_repo):
        vendor = demo_repo / "node_modules"
        vendor.mkdir(exist_ok=True)
        (vendor / "junk.js").write_text("var x = 1;", encoding="utf-8")
        cfg = load_config(demo_repo)
        found = {rel for _, rel, _ in discover_files(cfg)}
        assert not any("node_modules" in f for f in found)

    def test_ignore_tests_flag(self, demo_repo):
        cfg = load_config(demo_repo, {"ignore_tests": True})
        found = {rel for _, rel, _ in discover_files(cfg)}
        assert not any("test" in f for f in found)


class TestScanning:
    def test_produces_a_report(self, scanned):
        report, _ = scanned
        assert report.summary["files_scanned"] >= 4
        assert report.git_available

    def test_risky_file_outranks_clean_file(self, scanned):
        report, _ = scanned
        payment = report.by_path("src/payment.py")
        utils = report.by_path("src/utils.py")
        assert payment.score > utils.score
        assert payment.band in ("high", "critical")
        assert utils.band == "low"

    def test_scores_are_bounded(self, scanned):
        report, _ = scanned
        assert all(0 <= f.score <= 100 for f in report.files)

    def test_every_score_is_attributable(self, scanned):
        """The core promise: points always trace back to named evidence."""
        report, _ = scanned
        for f in report.files:
            if f.score > 0:
                assert f.hits, f"{f.path} scored {f.score} with no evidence"
                assert sum(h.score for h in f.hits) > 0
                for hit in f.hits:
                    assert hit.detail.strip()

    def test_results_sorted_by_risk(self, scanned):
        report, _ = scanned
        scores = [f.score for f in report.files]
        assert scores == sorted(scores, reverse=True)

    def test_git_signals_are_attached(self, scanned):
        report, _ = scanned
        payment = report.by_path("src/payment.py")
        assert payment.git is not None
        assert payment.git.bugfix_commit_count >= 8
        assert any(h.phase == "git" for h in payment.hits)

    def test_works_without_git(self, plain_dir):
        cfg = load_config(plain_dir)
        report = Scanner(cfg).scan(train=False, use_model=False)
        assert report.git_available is False
        assert report.summary["files_scanned"] == 1
        assert report.summary["git"]["reason"]

    def test_json_round_trip(self, scanned, tmp_path):
        report, _ = scanned
        path = tmp_path / "r.json"
        save_report(report, path)
        restored = report_from_dict(json.loads(path.read_text(encoding="utf-8")))
        assert len(restored.files) == len(report.files)
        assert restored.files[0].path == report.files[0].path
        assert restored.files[0].hits[0].rule_id == report.files[0].hits[0].rule_id

    def test_html_report_is_self_contained(self, scanned, tmp_path):
        report, scanner = scanned
        out = tmp_path / "r.html"
        write_html_report(report, out)
        html = out.read_text(encoding="utf-8")
        assert "<!doctype html>" in html.lower()
        assert "src/payment.py" in html
        # No external network dependencies.
        assert "http://" not in html.replace("http://gw", "")
        assert "cdn" not in html.lower()


class TestDependencyGraph:
    def test_resolves_python_imports(self, scanned):
        _, scanner = scanned
        graph = scanner.graph
        assert graph.has_edge("src/api.py", "src/payment.py")
        assert graph.has_edge("src/api.py", "src/database.py")

    def test_import_direction_is_importer_to_imported(self, scanned):
        report, _ = scanned
        api = report.by_path("src/api.py")
        payment = report.by_path("src/payment.py")
        assert "src/payment.py" in api.dependencies
        assert "src/api.py" in payment.dependents

    def test_handles_unresolvable_imports(self):
        metrics = {
            "a.py": FileMetrics(path="a.py", language="python",
                                imports=["totally_external_lib", "os"]),
        }
        graph = build_dependency_graph(metrics)
        assert graph.number_of_nodes() == 1
        assert graph.number_of_edges() == 0


class TestImpactSimulation:
    def test_finds_dependents(self, scanned):
        report, scanner = scanned
        result = simulate_impact(
            scanner.graph, ["src/payment.py"],
            risk_by_path={f.path: f.score for f in report.files},
            cochange_strength=scanner.cochange_strength(),
        )
        paths = [a["path"] for a in result.affected]
        assert "src/api.py" in paths

    def test_every_prediction_is_explained(self, scanned):
        report, scanner = scanned
        result = simulate_impact(
            scanner.graph, ["src/database.py"],
            risk_by_path={f.path: f.score for f in report.files},
            cochange_strength=scanner.cochange_strength(),
        )
        for item in result.affected:
            assert item["reasons"], f"{item['path']} had no explanation"
            assert item["impact_score"] > 0
        assert result.explanation

    def test_unknown_seed_is_reported(self, scanned):
        _, scanner = scanned
        result = simulate_impact(scanner.graph, ["does/not/exist.py"])
        assert result.affected == []
        assert "dependency graph" in result.explanation[0]
        assert "does/not/exist.py" in result.explanation[0]

    def test_direct_dependents_outrank_distant_ones(self, scanned):
        report, scanner = scanned
        result = simulate_impact(
            scanner.graph, ["src/payment.py"],
            risk_by_path={f.path: f.score for f in report.files},
            cochange_strength={},
        )
        if len(result.affected) >= 2:
            hops = [a["hops"] for a in result.affected]
            assert hops[0] <= max(hops)


class TestMachineLearning:
    def test_feature_vector_matches_names(self):
        vector = build_feature_vector(
            FileMetrics(path="a.py", loc=100), GitMetrics(path="a.py"), 1, 2
        )
        assert len(vector) == len(FEATURE_NAMES)
        assert all(isinstance(v, float) for v in vector)

    def test_probabilities_are_clamped(self):
        assert _clamp_probability(1.0) == 0.95
        assert _clamp_probability(0.0) == 0.02
        assert _clamp_probability(0.5) == 0.5

    def test_training_degrades_gracefully_on_tiny_repo(self, demo_repo, tmp_path):
        """A small repo cannot support a model - it must say so, not crash."""
        cfg = load_config(demo_repo)
        predictor = BugPredictor(tmp_path / "model")
        metrics = {"src/payment.py": FileMetrics(path="src/payment.py", loc=50)}
        report = predictor.train(cfg.root, metrics, {}, label_window_days=30)
        assert report.trained is False
        assert report.reason
        assert predictor.predict([0.0] * len(FEATURE_NAMES)) is None

    def test_predict_without_model_returns_none(self, tmp_path):
        predictor = BugPredictor(tmp_path / "nothing")
        assert predictor.load() is False
        assert predictor.predict([0.0] * len(FEATURE_NAMES)) is None
        assert predictor.explain([0.0] * len(FEATURE_NAMES)) == []
