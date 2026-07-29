"""OPTIONAL AI narrator.

BugSeer never needs this. Every score, every rule and every explanation is
computed deterministically offline. This module only takes evidence that has
*already been computed* and asks an LLM to phrase it as prose, for teams who
prefer a paragraph over a bullet list.

Guarantees:
  * It cannot change a risk score. It receives the finished evidence.
  * It sends metrics and rule names only, never source code, unless the user
    explicitly opts in via BUGSEER_AI_SEND_SOURCE=1.
  * BUGSEER_OFFLINE=1 blocks it entirely, even if a key is configured.
  * Any failure degrades gracefully back to the deterministic explanation.

Implemented with the stdlib only (urllib) so enabling it pulls in no new
dependency.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

SYSTEM_PROMPT = (
    "You are a senior code reviewer summarising a static-analysis report. "
    "You will receive pre-computed, factual evidence about one source file: "
    "metrics, triggered rules, and git history signals. "
    "Write 3-5 sentences explaining to the file's owner why it is risky and what "
    "to do first. Be concrete and reference the actual numbers you are given. "
    "Never invent facts that are not in the evidence. Never restate the numbers "
    "as a list - write prose. Do not use markdown headings."
)


@dataclass
class NarrationResult:
    text: str
    provider: str
    ok: bool
    error: str = ""


class NarratorUnavailable(Exception):
    """Raised when narration is requested but not configured."""


def _redact(path: str, enabled: bool) -> str:
    if not enabled:
        return path
    digest = hashlib.blake2b(path.encode("utf-8"), digest_size=6).hexdigest()
    suffix = path.rsplit(".", 1)[-1] if "." in path else "src"
    return f"file-{digest}.{suffix}"


def build_evidence_payload(file_risk: Any, *, include_source: bool = False,
                           redact_paths: bool = False,
                           source_text: str | None = None) -> dict[str, Any]:
    """Assemble the minimal, factual payload sent to the provider."""
    metrics = file_risk.metrics
    git = file_risk.git
    payload: dict[str, Any] = {
        "file": _redact(file_risk.path, redact_paths),
        "language": file_risk.language,
        "risk_score": round(file_risk.score, 1),
        "risk_band": file_risk.band,
        "triggered_rules": [
            {
                "rule": h.rule_id,
                "title": h.title,
                "points": round(h.score, 1),
                "detail": h.detail,
            }
            for h in file_risk.top_reasons(8)
        ],
    }
    if metrics:
        payload["metrics"] = {
            "lines_of_code": metrics.loc,
            "functions": metrics.function_count,
            "longest_function": metrics.max_function_length,
            "max_nesting": metrics.max_nesting_depth,
            "cyclomatic_complexity": metrics.cyclomatic_complexity,
            "try_blocks": metrics.try_blocks,
            "risky_calls": metrics.risky_calls,
            "globals": metrics.global_variables,
            "duplicate_ratio": round(metrics.duplicate_line_ratio, 3),
        }
    if git:
        payload["git"] = {
            "commits": git.commit_count,
            "bugfix_commits": git.bugfix_commit_count,
            "reverts": git.revert_count,
            "authors": git.author_count,
            "days_since_last_change": git.days_since_last_change,
            "fix_follow_rate": git.fix_follow_rate,
        }
    if file_risk.ml_probability is not None:
        payload["model_probability"] = round(file_risk.ml_probability, 3)
        payload["model_top_factors"] = [
            c["label"] for c in file_risk.ml_contributions[:4]
        ]
    if include_source and source_text:
        payload["source_excerpt"] = source_text[:6000]
    return payload


# --------------------------------------------------------------------------
# Providers (stdlib urllib only)
# --------------------------------------------------------------------------
def _post_json(url: str, headers: dict[str, str], body: dict[str, Any],
               timeout: int) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _call_openai(prompt: str, timeout: int) -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise NarratorUnavailable("OPENAI_API_KEY is not set")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    result = _post_json(
        f"{base}/chat/completions",
        {"Authorization": f"Bearer {key}"},
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 400,
        },
        timeout,
    )
    return result["choices"][0]["message"]["content"].strip()


def _call_anthropic(prompt: str, timeout: int) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise NarratorUnavailable("ANTHROPIC_API_KEY is not set")
    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    model = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
    result = _post_json(
        f"{base}/v1/messages",
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
        {
            "model": model,
            "max_tokens": 400,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout,
    )
    parts = result.get("content", [])
    return "".join(p.get("text", "") for p in parts).strip()


def _call_gemini(prompt: str, timeout: int) -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise NarratorUnavailable("GEMINI_API_KEY is not set")
    base = os.environ.get(
        "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com"
    ).rstrip("/")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    result = _post_json(
        f"{base}/v1beta/models/{model}:generateContent?key={key}",
        {},
        {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 400},
        },
        timeout,
    )
    candidates = result.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini returned no candidates")
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts).strip()


def _call_ollama(prompt: str, timeout: int) -> str:
    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "llama3.2")
    result = _post_json(
        f"{base}/api/chat",
        {},
        {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        },
        timeout,
    )
    return result.get("message", {}).get("content", "").strip()


_PROVIDERS = {
    "openai": _call_openai,
    "anthropic": _call_anthropic,
    "claude": _call_anthropic,
    "gemini": _call_gemini,
    "google": _call_gemini,
    "ollama": _call_ollama,
}


def detect_provider() -> str:
    """Resolve the configured provider, auto-detecting from whichever key is set."""
    explicit = (os.environ.get("BUGSEER_AI_PROVIDER") or "").strip().lower()
    if explicit and explicit != "none":
        return explicit
    if explicit == "none":
        return "none"
    for name, env in (("openai", "OPENAI_API_KEY"),
                      ("anthropic", "ANTHROPIC_API_KEY"),
                      ("gemini", "GEMINI_API_KEY")):
        if os.environ.get(env, "").strip():
            return name
    return "none"


def narrate(file_risk: Any, *, provider: str | None = None,
            send_source: bool | None = None, redact_paths: bool | None = None,
            timeout: int | None = None, source_text: str | None = None) -> NarrationResult:
    """Turn computed evidence into prose. Never raises; degrades gracefully."""
    if os.environ.get("BUGSEER_OFFLINE", "0").strip().lower() in {"1", "true", "yes", "on"}:
        return NarrationResult(
            "", "none", False,
            "BUGSEER_OFFLINE=1 blocks all outbound requests (this is the safe default "
            "for private code).",
        )

    provider = (provider or detect_provider()).lower()
    if provider in ("", "none"):
        return NarrationResult(
            "", "none", False,
            "No AI provider configured. Set BUGSEER_AI_PROVIDER and the matching key in "
            ".env (see .env.example) - entirely optional; BugSeer's analysis is already "
            "complete without it.",
        )

    fn = _PROVIDERS.get(provider)
    if fn is None:
        return NarrationResult(
            "", provider, False,
            f"Unknown provider '{provider}'. Valid: openai, anthropic, gemini, ollama.",
        )

    if send_source is None:
        send_source = os.environ.get("BUGSEER_AI_SEND_SOURCE", "0").strip() in {"1", "true", "yes"}
    if redact_paths is None:
        redact_paths = os.environ.get("BUGSEER_AI_REDACT_PATHS", "0").strip() in {"1", "true", "yes"}
    if timeout is None:
        try:
            timeout = int(os.environ.get("BUGSEER_AI_TIMEOUT", "30"))
        except ValueError:
            timeout = 30

    payload = build_evidence_payload(
        file_risk, include_source=send_source, redact_paths=redact_paths,
        source_text=source_text,
    )
    prompt = (
        "Here is the pre-computed static-analysis and git-history evidence for one "
        "file. Summarise why it is risky and what the owner should fix first.\n\n"
        + json.dumps(payload, indent=2)
    )

    try:
        text = fn(prompt, timeout)
        if not text:
            return NarrationResult("", provider, False, "Provider returned an empty response.")
        return NarrationResult(text, provider, True)
    except NarratorUnavailable as exc:
        return NarrationResult("", provider, False, str(exc))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        return NarrationResult("", provider, False, f"HTTP {exc.code} from {provider}: {detail}")
    except urllib.error.URLError as exc:
        return NarrationResult("", provider, False, f"Network error: {exc.reason}")
    except Exception as exc:  # noqa: BLE001
        return NarrationResult("", provider, False, f"{type(exc).__name__}: {exc}")
