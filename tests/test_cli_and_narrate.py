"""CLI behaviour and the optional AI narrator's safety guarantees."""

from __future__ import annotations

import json
import os

import pytest
from typer.testing import CliRunner

from bugseer.cli import app
from bugseer.models import FileRisk, RuleHit
from bugseer.narrate import build_evidence_payload, detect_provider, narrate

runner = CliRunner()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Never let a developer's real keys leak into the test run."""
    for key in ("BUGSEER_AI_PROVIDER", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                "GEMINI_API_KEY", "BUGSEER_OFFLINE", "BUGSEER_AI_SEND_SOURCE",
                "BUGSEER_AI_REDACT_PATHS"):
        monkeypatch.delenv(key, raising=False)


class TestCli:
    def test_version(self):
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "BugSeer" in result.stdout

    def test_scan(self, demo_repo):
        result = runner.invoke(app, ["scan", str(demo_repo), "--top", "5"])
        assert result.exit_code == 0
        assert "payment.py" in result.stdout

    def test_scan_json_output(self, demo_repo, tmp_path):
        out = tmp_path / "r.json"
        result = runner.invoke(app, ["scan", str(demo_repo), "--json", str(out)])
        assert result.exit_code == 0
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["summary"]["files_scanned"] >= 4
        assert data["files"][0]["hits"]

    def test_scan_html_output(self, demo_repo, tmp_path):
        out = tmp_path / "r.html"
        result = runner.invoke(app, ["scan", str(demo_repo), "--html", str(out)])
        assert result.exit_code == 0
        assert out.is_file()

    def test_fail_over_gate_trips(self, demo_repo):
        result = runner.invoke(app, ["scan", str(demo_repo), "--fail-over", "1"])
        assert result.exit_code == 1
        assert "FAIL" in result.stdout

    def test_fail_over_gate_passes(self, demo_repo):
        result = runner.invoke(app, ["scan", str(demo_repo), "--fail-over", "100"])
        assert result.exit_code == 0
        assert "PASS" in result.stdout

    def test_heatmap(self, demo_repo):
        result = runner.invoke(app, ["heatmap", str(demo_repo)])
        assert result.exit_code == 0
        assert "payment.py" in result.stdout

    def test_explain(self, demo_repo):
        result = runner.invoke(
            app, ["explain", "src/payment.py", "--repo", str(demo_repo), "--fresh"]
        )
        assert result.exit_code == 0
        assert "payment.py" in result.stdout

    def test_explain_unknown_file(self, demo_repo):
        result = runner.invoke(
            app, ["explain", "nope.py", "--repo", str(demo_repo), "--fresh"]
        )
        assert result.exit_code == 2

    def test_impact(self, demo_repo):
        result = runner.invoke(
            app, ["impact", "src/payment.py", "--repo", str(demo_repo)]
        )
        assert result.exit_code == 0

    def test_init_creates_config(self, tmp_path):
        result = runner.invoke(app, ["init", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / ".bugseer.toml").is_file()

    def test_init_refuses_to_overwrite(self, tmp_path):
        (tmp_path / ".bugseer.toml").write_text("x", encoding="utf-8")
        assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 1


def make_file_risk() -> FileRisk:
    return FileRisk(
        path="src/payment.py",
        language="python",
        score=88.0,
        band="critical",
        hits=[RuleHit(rule_id="no-error-handling", title="No error handling",
                      score=20.0, detail="4 risky calls, no try block")],
    )


class TestNarratorSafety:
    def test_disabled_by_default(self):
        assert detect_provider() == "none"
        result = narrate(make_file_risk())
        assert result.ok is False
        assert "No AI provider configured" in result.error

    def test_offline_flag_blocks_even_with_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-should-never-be-used")
        monkeypatch.setenv("BUGSEER_AI_PROVIDER", "openai")
        monkeypatch.setenv("BUGSEER_OFFLINE", "1")
        result = narrate(make_file_risk())
        assert result.ok is False
        assert "BUGSEER_OFFLINE" in result.error

    def test_provider_autodetected_from_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        assert detect_provider() == "anthropic"

    def test_explicit_none_wins_over_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("BUGSEER_AI_PROVIDER", "none")
        assert detect_provider() == "none"

    def test_payload_excludes_source_by_default(self):
        payload = build_evidence_payload(make_file_risk(), source_text="SECRET_CODE")
        assert "source_excerpt" not in payload
        assert "SECRET_CODE" not in json.dumps(payload)

    def test_payload_includes_source_only_when_opted_in(self):
        payload = build_evidence_payload(
            make_file_risk(), include_source=True, source_text="SECRET_CODE"
        )
        assert payload["source_excerpt"] == "SECRET_CODE"

    def test_path_redaction(self):
        payload = build_evidence_payload(make_file_risk(), redact_paths=True)
        assert payload["file"] != "src/payment.py"
        assert payload["file"].endswith(".py")

    def test_payload_carries_the_evidence(self):
        payload = build_evidence_payload(make_file_risk())
        assert payload["risk_score"] == 88.0
        assert payload["triggered_rules"][0]["rule"] == "no-error-handling"

    def test_unknown_provider_is_reported(self, monkeypatch):
        monkeypatch.setenv("BUGSEER_AI_PROVIDER", "notreal")
        result = narrate(make_file_risk())
        assert result.ok is False
        assert "Unknown provider" in result.error

    def test_missing_key_never_raises(self, monkeypatch):
        monkeypatch.setenv("BUGSEER_AI_PROVIDER", "openai")
        result = narrate(make_file_risk())
        assert result.ok is False
        assert "OPENAI_API_KEY" in result.error
