"""Phase 2: git intelligence, plus coverage parsing."""

from __future__ import annotations

from pathlib import Path

from bugseer.git_intel import (
    BUGFIX_PATTERN, GitAnalyzer, NON_FIX_PREFIX, is_git_repo,
    infer_test_coverage_by_convention, parse_coverage, parse_log,
)


class TestBugfixDetection:
    def test_recognises_fix_language(self):
        for subject in ("fix: null pointer", "Fixed the crash", "hotfix login",
                        "resolve regression in parser", "patch memory leak",
                        "correct broken redirect"):
            assert BUGFIX_PATTERN.search(subject), subject

    def test_ignores_feature_and_chore_commits(self):
        for subject in ("feat: add dashboard", "docs: update readme",
                        "chore(deps): bump lib", "refactor: extract helper",
                        "test: add coverage", "ci: pin runner"):
            assert NON_FIX_PREFIX.match(subject), subject


class TestGitAnalyzer:
    def test_detects_repository(self, demo_repo, plain_dir):
        assert is_git_repo(demo_repo)
        assert not is_git_repo(plain_dir)

    def test_parses_commits(self, demo_repo):
        commits = parse_log(demo_repo, history_days=0)
        assert len(commits) >= 10
        assert all(c.sha and c.author and c.timestamp for c in commits)

    def test_identifies_risky_file(self, demo_repo):
        metrics = GitAnalyzer(demo_repo, history_days=0).analyze()
        payment = metrics["src/payment.py"]
        utils = metrics["src/utils.py"]
        assert payment.commit_count > utils.commit_count
        assert payment.bugfix_commit_count >= 8
        assert payment.bugfix_ratio > utils.bugfix_ratio

    def test_detects_reverts(self, demo_repo):
        analyzer = GitAnalyzer(demo_repo, history_days=0)
        analyzer.analyze()
        assert analyzer.repo_summary()["reverts"] >= 1

    def test_repo_summary_shape(self, demo_repo):
        analyzer = GitAnalyzer(demo_repo, history_days=0)
        analyzer.analyze()
        summary = analyzer.repo_summary()
        assert summary["available"]
        assert summary["commits_analyzed"] > 0
        assert 0 <= summary["bugfix_ratio"] <= 1

    def test_non_repo_reports_reason(self, plain_dir):
        analyzer = GitAnalyzer(plain_dir)
        assert analyzer.analyze() == {}
        assert "not a git repository" in analyzer.unavailable_reason

    def test_cochange_partners_recorded(self, demo_repo):
        analyzer = GitAnalyzer(demo_repo, history_days=0)
        metrics = analyzer.analyze()
        # The initial commit touched every file, so partners must exist.
        assert any(m.co_change_partners for m in metrics.values())


class TestCoverageParsing:
    def test_cobertura(self, tmp_path):
        (tmp_path / "coverage.xml").write_text(
            '<?xml version="1.0"?><coverage><packages><package><classes>'
            '<class filename="src/a.py" line-rate="0.25"/>'
            '<class filename="src/b.py" line-rate="0.9"/>'
            "</classes></package></packages></coverage>",
            encoding="utf-8",
        )
        result = parse_coverage(tmp_path / "coverage.xml", tmp_path)
        assert result["src/a.py"] == 0.25
        assert result["src/b.py"] == 0.9

    def test_lcov(self, tmp_path):
        (tmp_path / "lcov.info").write_text(
            "SF:src/a.js\nLF:100\nLH:30\nend_of_record\n"
            "SF:src/b.js\nLF:50\nLH:50\nend_of_record\n",
            encoding="utf-8",
        )
        result = parse_coverage(tmp_path / "lcov.info", tmp_path)
        assert result["src/a.js"] == 0.3
        assert result["src/b.js"] == 1.0

    def test_coverage_py_json(self, tmp_path):
        (tmp_path / "coverage.json").write_text(
            '{"files": {"src/a.py": {"summary": {"percent_covered": 42.0}}}}',
            encoding="utf-8",
        )
        result = parse_coverage(tmp_path / "coverage.json", tmp_path)
        assert abs(result["src/a.py"] - 0.42) < 1e-9

    def test_corrupt_report_returns_empty(self, tmp_path):
        (tmp_path / "coverage.xml").write_text("<not-xml", encoding="utf-8")
        assert parse_coverage(tmp_path / "coverage.xml", tmp_path) == {}


class TestTestConvention:
    def test_matches_test_files_by_name(self):
        files = ["src/utils.py", "src/payment.py", "tests/test_utils.py"]
        result = infer_test_coverage_by_convention(
            files, lambda p: "test" in p.lower()
        )
        assert result["src/utils.py"] is True
        assert result["src/payment.py"] is False
        assert "tests/test_utils.py" not in result
