"""The rule engine: scoring, explainability and configurability."""

from __future__ import annotations

from bugseer.config import Config, load_config
from bugseer.models import FileMetrics, GitMetrics
from bugseer.rules import (
    band_for, evaluate_git, evaluate_graph, evaluate_static, squash,
)


def make_metrics(**kwargs) -> FileMetrics:
    base = dict(path="a.py", language="python", loc=200, function_count=5)
    base.update(kwargs)
    return FileMetrics(**base)


class TestStaticRules:
    def test_clean_file_triggers_nothing(self):
        cfg = Config()
        metrics = make_metrics(loc=50, cyclomatic_complexity=3, comment_ratio=0.2)
        hits = evaluate_static(metrics, cfg, coverage=0.9)
        assert hits == []

    def test_long_function_fires_with_location(self):
        cfg = Config()
        metrics = make_metrics(
            max_function_length=250,
            long_functions=[{"name": "big", "line": 10, "end_line": 260,
                             "length": 250, "params": 2, "complexity": 5}],
        )
        hits = evaluate_static(metrics, cfg)
        hit = next(h for h in hits if h.rule_id == "long-function")
        assert hit.score >= cfg.weights.long_function
        assert hit.locations[0]["line"] == 10
        assert "big" in hit.detail

    def test_no_error_handling_requires_risky_calls(self):
        cfg = Config()
        risky = evaluate_static(make_metrics(risky_calls=6, try_blocks=0), cfg)
        assert any(h.rule_id == "no-error-handling" for h in risky)
        guarded = evaluate_static(make_metrics(risky_calls=6, try_blocks=3), cfg)
        assert not any(h.rule_id == "no-error-handling" for h in guarded)

    def test_coverage_rule_uses_measured_value(self):
        cfg = Config()
        hits = evaluate_static(make_metrics(), cfg, coverage=0.05)
        hit = next(h for h in hits if h.rule_id == "low-coverage")
        assert hit.evidence["source"] == "coverage report"

    def test_missing_test_file_is_labelled_a_heuristic(self):
        cfg = Config()
        hits = evaluate_static(make_metrics(), cfg, coverage=None, has_test_file=False)
        hit = next(h for h in hits if h.rule_id == "no-test-file")
        assert hit.evidence["source"] == "naming heuristic"
        assert "heuristic" in hit.detail.lower()

    def test_every_hit_is_explainable(self):
        cfg = Config()
        metrics = make_metrics(
            loc=900, risky_calls=9, try_blocks=0, global_variables=4,
            nested_loop_depth=5, branch_count=40, cyclomatic_complexity=60,
            max_function_complexity=45, bare_excepts=2, magic_numbers=30,
            todo_comments=9, max_parameters=10, mutable_default_args=2,
            duplicate_line_ratio=0.4, duplicate_block_count=6,
            max_function_length=300,
            long_functions=[{"name": "f", "line": 1, "end_line": 300,
                             "length": 300, "params": 10, "complexity": 45}],
        )
        hits = evaluate_static(metrics, cfg, coverage=0.0)
        assert len(hits) >= 8
        for hit in hits:
            assert hit.score > 0
            assert hit.detail.strip()
            assert hit.rule_id and hit.title
            assert hit.phase == "static"

    def test_trivial_file_is_not_flagged_as_untested(self):
        """An empty or constants-only file must not generate noise."""
        cfg = Config()
        trivial = make_metrics(loc=2, function_count=0, class_count=0)
        assert evaluate_static(trivial, cfg, coverage=None, has_test_file=False) == []

    def test_unreadable_file_scores_nothing(self):
        """Binary/oversized files carry no evidence, so they get no score."""
        cfg = Config()
        skipped = make_metrics(parse_ok=False, parse_error="skipped: binary file")
        assert evaluate_static(skipped, cfg, coverage=None, has_test_file=False) == []

    def test_real_module_without_tests_is_still_flagged(self):
        cfg = Config()
        real = make_metrics(loc=200, function_count=6)
        hits = evaluate_static(real, cfg, coverage=None, has_test_file=False)
        assert any(h.rule_id == "no-test-file" for h in hits)

    def test_weights_are_configurable(self):
        default = Config()
        custom = Config()
        custom.weights.global_variables = 60.0
        metrics = make_metrics(global_variables=3)
        base = next(h for h in evaluate_static(metrics, default, coverage=1.0)
                    if h.rule_id == "global-state")
        raised = next(h for h in evaluate_static(metrics, custom, coverage=1.0)
                      if h.rule_id == "global-state")
        assert raised.score > base.score

    def test_thresholds_are_configurable(self):
        cfg = Config()
        cfg.thresholds.long_function_lines = 500
        metrics = make_metrics(
            max_function_length=250,
            long_functions=[{"name": "f", "line": 1, "end_line": 250,
                             "length": 250, "params": 1, "complexity": 3}],
        )
        assert not any(h.rule_id == "long-function"
                       for h in evaluate_static(metrics, cfg, coverage=1.0))


class TestGitRules:
    def test_no_history_no_hits(self):
        assert evaluate_git(GitMetrics(path="a.py"), Config()) == []

    def test_bugfix_density_fires(self):
        cfg = Config()
        git = GitMetrics(path="a.py", commit_count=40, bugfix_commit_count=20,
                         bugfix_ratio=0.5)
        hit = next(h for h in evaluate_git(git, cfg) if h.rule_id == "bugfix-density")
        assert hit.phase == "git"
        assert "20" in hit.detail

    def test_revert_history_fires(self):
        git = GitMetrics(path="a.py", commit_count=10, revert_count=2)
        hits = evaluate_git(git, Config())
        assert any(h.rule_id == "revert-history" for h in hits)

    def test_fix_follow_rate_fires(self):
        git = GitMetrics(path="a.py", commit_count=20, fix_follow_rate=0.6)
        hit = next(h for h in evaluate_git(git, Config())
                   if h.rule_id == "fix-follow-rate")
        assert "60%" in hit.detail

    def test_knowledge_silo_only_for_single_owner(self):
        silo = GitMetrics(path="a.py", commit_count=20, author_count=1,
                          ownership_ratio=1.0, authors=["solo"])
        assert any(h.rule_id == "knowledge-silo" for h in evaluate_git(silo, Config()))
        shared = GitMetrics(path="a.py", commit_count=20, author_count=8,
                            ownership_ratio=0.3)
        hits = evaluate_git(shared, Config())
        assert any(h.rule_id == "many-authors" for h in hits)
        assert not any(h.rule_id == "knowledge-silo" for h in hits)


class TestGraphRules:
    def test_high_fan_in(self):
        dependents = [f"m{i}.py" for i in range(20)]
        hit = next(h for h in evaluate_graph("core.py", dependents, 0.0, Config())
                   if h.rule_id == "high-fan-in")
        assert hit.evidence["fan_in"] == 20
        assert hit.phase == "graph"

    def test_low_fan_in_is_quiet(self):
        assert evaluate_graph("leaf.py", ["a.py"], 0.0, Config()) == []


class TestScoring:
    def test_squash_is_monotonic_and_bounded(self):
        scores = [squash(v) for v in (0, 10, 40, 90, 200, 600)]
        assert scores[0] == 0.0
        assert all(b >= a for a, b in zip(scores, scores[1:]))
        assert scores[-1] <= 100.0

    def test_bands_follow_thresholds(self):
        cfg = Config()
        assert band_for(5, cfg) == "low"
        assert band_for(45, cfg) == "medium"
        assert band_for(70, cfg) == "high"
        assert band_for(95, cfg) == "critical"


class TestConfigLoading(object):
    def test_toml_overrides_defaults(self, tmp_path):
        (tmp_path / ".bugseer.toml").write_text(
            "[bugseer]\nhistory_days = 90\n"
            "[bugseer.weights]\nglobal_variables = 99\n"
            "[bugseer.thresholds]\nband_high = 70\n",
            encoding="utf-8",
        )
        cfg = load_config(tmp_path)
        assert cfg.history_days == 90
        assert cfg.weights.global_variables == 99.0
        assert cfg.thresholds.band_high == 70

    def test_malformed_toml_does_not_crash(self, tmp_path):
        (tmp_path / ".bugseer.toml").write_text("this is not [valid toml", encoding="utf-8")
        cfg = load_config(tmp_path)
        assert cfg.history_days == 730  # fell back to the default

    def test_cli_overrides_win(self, tmp_path):
        (tmp_path / ".bugseer.toml").write_text(
            "[bugseer]\nhistory_days = 90\n", encoding="utf-8"
        )
        cfg = load_config(tmp_path, {"history_days": 5})
        assert cfg.history_days == 5

    def test_user_excludes_extend_defaults(self, tmp_path):
        (tmp_path / ".bugseer.toml").write_text(
            '[bugseer]\nexclude = ["docs/*"]\n', encoding="utf-8"
        )
        cfg = load_config(tmp_path)
        assert "docs/*" in cfg.exclude
        assert "node_modules/*" in cfg.exclude
