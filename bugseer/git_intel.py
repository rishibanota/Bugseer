"""Phase 2: git intelligence.

Everything here is computed by shelling out to the local `git` binary. There is
no network access, no API, and no GitPython requirement (though GitPython is
supported if installed, it is never needed).

Signals extracted per file:
  * commit count, churn, authorship spread and ownership concentration
  * bug-fix commit density (commits whose message looks like a fix)
  * revert history
  * "fix-follow rate": how often an edit is followed by a bug fix within N days
  * co-change partners: files that are habitually committed together
"""

from __future__ import annotations

import os
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from bugseer.models import GitMetrics

# Commit subjects that signal a defect repair. Deliberately broad: recall
# matters more than precision because the ratio is what feeds the score.
BUGFIX_PATTERN = re.compile(
    r"\b("
    r"fix(e[sd])?|bugfix|hotfix|patch(ed|es)?|repair(ed)?|resolve[sd]?|"
    r"bug|defect|issue|error|crash(es|ed)?|fail(ure|ing|ed)?|broken|break(s|age)?|"
    r"regress(ion|ed)?|incorrect|invalid|wrong|mistake|typo|"
    r"workaround|revert(ed|s)?|rollback|"
    r"npe|nullpointer|segfault|deadlock|race\s*condition|memory\s*leak|"
    r"oops|whoops|mitigat(e|ion)"
    r")\b",
    re.IGNORECASE,
)

REVERT_PATTERN = re.compile(r"^\s*(revert|rollback|back\s*out)\b", re.IGNORECASE)

# Conventional-commit prefixes that are definitely NOT bug fixes.
NON_FIX_PREFIX = re.compile(
    r"^\s*(feat|feature|docs?|style|refactor|perf|test|chore|build|ci|release)\b[:(]",
    re.IGNORECASE,
)

_RECORD_SEP = "\x1e"
_FIELD_SEP = "\x1f"


@dataclass
class Commit:
    sha: str
    author: str
    email: str
    timestamp: int
    subject: str
    files: list[str]
    insertions: dict[str, int]
    deletions: dict[str, int]

    @property
    def is_bugfix(self) -> bool:
        if NON_FIX_PREFIX.match(self.subject):
            return False
        return bool(BUGFIX_PATTERN.search(self.subject))

    @property
    def is_revert(self) -> bool:
        return bool(REVERT_PATTERN.match(self.subject))


def _run_git_raw(args: list[str], cwd: Path, timeout: int = 120) -> tuple[int, str]:
    """Run a git command, returning (returncode, stdout).

    Returns rc=-1 for a timeout and rc=-2 when git could not be executed, so
    callers can distinguish "git said no" from "git never ran".
    """
    env = {
        **os.environ,
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
        # Never let a history read block on credentials or the network.
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
    }
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True,
            timeout=timeout, check=False, env=env,
        )
    except subprocess.TimeoutExpired:
        return -1, ""
    except OSError:
        return -2, ""
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace")


def _run_git(args: list[str], cwd: Path, timeout: int = 120) -> str:
    """Run a git command, returning stdout ('' on any failure)."""
    rc, out = _run_git_raw(args, cwd, timeout)
    return out if rc == 0 else ""


def is_partial_clone(root: Path) -> bool:
    """Detect a blobless/treeless clone.

    These are common in CI (`git clone --filter=blob:none`). On such a repo
    `git log --numstat` has to fetch every blob from the remote, which is slow
    and fails outright when offline - so BugSeer must not rely on it.
    """
    for key in ("remote.origin.promisor", "remote.origin.partialclonefilter"):
        rc, out = _run_git_raw(["config", "--get", key], root, timeout=15)
        if rc == 0 and out.strip():
            return True
    return False


def is_git_repo(root: Path) -> bool:
    return _run_git(["rev-parse", "--is-inside-work-tree"], root).strip() == "true"


def repo_root(root: Path) -> Path:
    out = _run_git(["rev-parse", "--show-toplevel"], root).strip()
    return Path(out) if out else root


def parse_log(root: Path, history_days: int = 730, max_commits: int = 20000,
              timeout: int = 180) -> list[Commit]:
    """Parse the git log into structured commits.

    Prefers `--numstat` (which yields per-file line counts). Falls back to
    `--name-only` when numstat is unavailable or too slow - notably on partial
    clones, where numstat must fetch every blob from the remote. The fallback
    loses churn figures but preserves every other signal (commit counts,
    bug-fix density, reverts, authorship, co-change), so Phase 2 still works.
    """
    fmt = _RECORD_SEP + _FIELD_SEP.join(["%H", "%an", "%ae", "%at", "%s"])
    base_args = [
        "log", f"--pretty=format:{fmt}", "--no-merges",
        f"--max-count={max_commits}", "--no-renames",
    ]
    if history_days and history_days > 0:
        base_args.append(f"--since={history_days}.days.ago")

    raw = ""
    has_churn = True

    # Skip numstat entirely on a partial clone: it would stall on blob fetches.
    if not is_partial_clone(root):
        rc, out = _run_git_raw([*base_args, "--numstat"], root, timeout=timeout)
        if rc == 0 and out.strip():
            raw = out

    if not raw:
        rc, out = _run_git_raw([*base_args, "--name-only"], root, timeout=timeout)
        if rc == 0 and out.strip():
            raw = out
            has_churn = False

    if not raw:
        return []

    commits: list[Commit] = []
    for chunk in raw.split(_RECORD_SEP):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        header, _, body = chunk.partition("\n")
        parts = header.split(_FIELD_SEP)
        if len(parts) < 5:
            continue
        sha, author, email, ts, subject = parts[0], parts[1], parts[2], parts[3], parts[4]
        try:
            timestamp = int(ts)
        except ValueError:
            continue

        files: list[str] = []
        insertions: dict[str, int] = {}
        deletions: dict[str, int] = {}
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            if has_churn:
                cols = line.split("\t")
                if len(cols) != 3:
                    continue
                add_s, del_s, path = cols
                path = path.strip()
                if not path:
                    continue
                files.append(path)
                insertions[path] = int(add_s) if add_s.isdigit() else 0
                deletions[path] = int(del_s) if del_s.isdigit() else 0
            else:
                # --name-only: one bare path per line, no churn figures.
                path = line
                files.append(path)
                insertions[path] = 0
                deletions[path] = 0

        commits.append(
            Commit(sha=sha, author=author, email=email, timestamp=timestamp,
                   subject=subject, files=files, insertions=insertions, deletions=deletions)
        )
    return commits


def tracked_files(root: Path) -> set[str]:
    out = _run_git(["ls-files"], root, timeout=120)
    return {line.strip() for line in out.splitlines() if line.strip()}


class GitAnalyzer:
    """Turns a commit list into per-file metrics and a co-change graph."""

    def __init__(self, root: Path, history_days: int = 730,
                 extra_bugfix_pattern: str = "") -> None:
        self.root = repo_root(Path(root))
        self.history_days = history_days
        self.available = is_git_repo(Path(root))
        self.commits: list[Commit] = []
        self.unavailable_reason: str = ""
        self.degraded: bool = False
        self.extra_pattern = (
            re.compile(extra_bugfix_pattern, re.IGNORECASE) if extra_bugfix_pattern else None
        )
        self._metrics: dict[str, GitMetrics] = {}
        self._cochange: dict[str, Counter] = defaultdict(Counter)
        self._author_files: dict[str, set[str]] = defaultdict(set)

    # ------------------------------------------------------------------
    def _is_bugfix(self, commit: Commit) -> bool:
        if self.extra_pattern and self.extra_pattern.search(commit.subject):
            return True
        return commit.is_bugfix

    def analyze(self) -> dict[str, GitMetrics]:
        if not self.available:
            self.unavailable_reason = "not a git repository"
            return {}

        if is_partial_clone(self.root):
            # Signals still work, but per-file churn is unavailable.
            self.degraded = True

        self.commits = parse_log(self.root, self.history_days)
        if not self.commits:
            if self.history_days:
                self.unavailable_reason = (
                    f"no commits found in the last {self.history_days} days "
                    "(try --history-days 0 for the full history)"
                )
            else:
                self.unavailable_reason = "git log returned no commits (shallow clone?)"
            return {}

        now = datetime.now(timezone.utc).timestamp()
        tracked = tracked_files(self.root)

        stats: dict[str, dict] = defaultdict(
            lambda: {
                "commits": 0, "bugfix": 0, "reverts": 0, "adds": 0, "dels": 0,
                "authors": Counter(), "timestamps": [], "fix_after_edit": 0,
                "edits_considered": 0,
            }
        )

        # Chronological order makes the fix-follow computation a single pass.
        ordered = sorted(self.commits, key=lambda c: c.timestamp)

        # For each file, remember the timestamps of its bug-fix commits so we
        # can ask "was this edit followed by a fix soon after?".
        fix_times: dict[str, list[int]] = defaultdict(list)
        for commit in ordered:
            if self._is_bugfix(commit):
                for path in commit.files:
                    fix_times[path].append(commit.timestamp)

        seven_days = 7 * 86400
        for commit in ordered:
            is_fix = self._is_bugfix(commit)
            is_revert = commit.is_revert
            for path in commit.files:
                s = stats[path]
                s["commits"] += 1
                s["adds"] += commit.insertions.get(path, 0)
                s["dels"] += commit.deletions.get(path, 0)
                s["authors"][commit.author or commit.email] += 1
                s["timestamps"].append(commit.timestamp)
                if is_fix:
                    s["bugfix"] += 1
                if is_revert:
                    s["reverts"] += 1
                self._author_files[commit.author or commit.email].add(path)

                if not is_fix:
                    s["edits_considered"] += 1
                    later_fixes = fix_times.get(path, ())
                    for fix_ts in later_fixes:
                        if commit.timestamp < fix_ts <= commit.timestamp + seven_days:
                            s["fix_after_edit"] += 1
                            break

            # Co-change graph. Skip sweeping commits (formatting, mass renames)
            # which would otherwise link everything to everything.
            if 2 <= len(commit.files) <= 25:
                for i, path_a in enumerate(commit.files):
                    for path_b in commit.files[i + 1 :]:
                        self._cochange[path_a][path_b] += 1
                        self._cochange[path_b][path_a] += 1

        for path, s in stats.items():
            timestamps = sorted(s["timestamps"])
            last = timestamps[-1] if timestamps else 0
            first = timestamps[0] if timestamps else 0
            authors: Counter = s["authors"]
            top_author_commits = authors.most_common(1)[0][1] if authors else 0
            commit_count = s["commits"]

            partners = [
                {"path": partner, "count": count,
                 "strength": round(count / max(1, commit_count), 3)}
                for partner, count in self._cochange.get(path, Counter()).most_common(10)
            ]

            self._metrics[path] = GitMetrics(
                path=path,
                commit_count=commit_count,
                bugfix_commit_count=s["bugfix"],
                revert_count=s["reverts"],
                author_count=len(authors),
                authors=[a for a, _ in authors.most_common(10)],
                lines_added=s["adds"],
                lines_deleted=s["dels"],
                churn=s["adds"] + s["dels"],
                days_since_last_change=round((now - last) / 86400, 2) if last else 999.0,
                days_since_created=round((now - first) / 86400, 2) if first else 999.0,
                recent_commits_30d=sum(1 for t in timestamps if now - t <= 30 * 86400),
                recent_commits_90d=sum(1 for t in timestamps if now - t <= 90 * 86400),
                bugfix_ratio=round(s["bugfix"] / max(1, commit_count), 4),
                ownership_ratio=round(top_author_commits / max(1, commit_count), 4),
                fix_follow_rate=round(
                    s["fix_after_edit"] / max(1, s["edits_considered"]), 4
                ),
                co_change_partners=partners,
                tracked=path in tracked,
            )

        # Attach the most recent commit subject for context in explanations.
        for commit in reversed(ordered):
            for path in commit.files:
                gm = self._metrics.get(path)
                if gm is not None and not gm.last_commit_hash:
                    gm.last_commit_hash = commit.sha[:10]
                    gm.last_commit_subject = commit.subject[:160]

        return self._metrics

    # ------------------------------------------------------------------
    def metrics_for(self, path: str) -> GitMetrics:
        return self._metrics.get(path) or GitMetrics(path=path)

    def cochange_partners(self, path: str, limit: int = 20) -> list[tuple[str, int]]:
        return self._cochange.get(path, Counter()).most_common(limit)

    def coupled_developers(self, limit: int = 10) -> list[dict]:
        """Developers who repeatedly touch the same files (knowledge overlap)."""
        authors = list(self._author_files)
        pairs: list[dict] = []
        for i, a in enumerate(authors):
            for b in authors[i + 1 :]:
                shared = self._author_files[a] & self._author_files[b]
                if len(shared) >= 3:
                    pairs.append({
                        "authors": [a, b],
                        "shared_files": len(shared),
                        "examples": sorted(shared)[:5],
                    })
        pairs.sort(key=lambda p: p["shared_files"], reverse=True)
        return pairs[:limit]

    def repo_summary(self) -> dict:
        if not self.commits:
            return {"available": False, "reason": self.unavailable_reason}
        fixes = sum(1 for c in self.commits if self._is_bugfix(c))
        reverts = sum(1 for c in self.commits if c.is_revert)
        authors = Counter(c.author for c in self.commits)
        span_days = 0.0
        if self.commits:
            times = [c.timestamp for c in self.commits]
            span_days = round((max(times) - min(times)) / 86400, 1)
        return {
            "available": True,
            "commits_analyzed": len(self.commits),
            "bugfix_commits": fixes,
            "bugfix_ratio": round(fixes / max(1, len(self.commits)), 4),
            "reverts": reverts,
            "contributors": len(authors),
            "top_contributors": [
                {"author": a, "commits": n} for a, n in authors.most_common(5)
            ],
            "history_span_days": span_days,
            "degraded": self.degraded,
            "degraded_reason": (
                "partial clone detected - per-file churn is unavailable, all other "
                "git signals are intact" if self.degraded else ""
            ),
        }


# --------------------------------------------------------------------------
# Coverage parsing (feeds the "low test coverage" rule)
# --------------------------------------------------------------------------
def find_coverage_file(root: Path, explicit: str | None = None) -> Path | None:
    if explicit:
        p = Path(explicit)
        p = p if p.is_absolute() else root / p
        return p if p.is_file() else None
    for candidate in ("coverage.xml", "cobertura.xml", "lcov.info", "coverage/lcov.info",
                      "coverage.json", "coverage-final.json", "clover.xml"):
        p = root / candidate
        if p.is_file():
            return p
    return None


def parse_coverage(path: Path, root: Path) -> dict[str, float]:
    """Parse a coverage report into {relative_path: covered_fraction}."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    name = path.name.lower()
    if name.endswith(".xml"):
        return _parse_cobertura(text, root)
    if name.endswith(".info"):
        return _parse_lcov(text, root)
    if name.endswith(".json"):
        return _parse_coverage_json(text, root)
    return {}


def _normalize(path_str: str, root: Path) -> str:
    p = Path(path_str)
    try:
        if p.is_absolute():
            return str(p.resolve().relative_to(root.resolve())).replace("\\", "/")
    except (ValueError, OSError):
        pass
    return str(p).replace("\\", "/").lstrip("./")


def _parse_cobertura(text: str, root: Path) -> dict[str, float]:
    import xml.etree.ElementTree as ET
    try:
        tree = ET.fromstring(text)
    except ET.ParseError:
        return {}
    sources = [s.text.strip() for s in tree.findall(".//source") if s.text]
    out: dict[str, float] = {}
    for cls in tree.iter("class"):
        filename = cls.get("filename")
        if not filename:
            continue
        rate = cls.get("line-rate")
        try:
            value = float(rate) if rate is not None else 0.0
        except ValueError:
            value = 0.0
        key = _normalize(filename, root)
        out[key] = value
        for src in sources:
            out[_normalize(str(Path(src) / filename), root)] = value
    return out


def _parse_lcov(text: str, root: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    current: str | None = None
    found = hit = 0
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("SF:"):
            current = _normalize(line[3:], root)
            found = hit = 0
        elif line.startswith("LF:"):
            found = int(line[3:] or 0)
        elif line.startswith("LH:"):
            hit = int(line[3:] or 0)
        elif line == "end_of_record" and current:
            out[current] = (hit / found) if found else 0.0
            current = None
    return out


def _parse_coverage_json(text: str, root: Path) -> dict[str, float]:
    import json
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    out: dict[str, float] = {}
    files = data.get("files")
    if isinstance(files, dict):  # coverage.py format
        for filename, info in files.items():
            summary = info.get("summary", {}) if isinstance(info, dict) else {}
            pct = summary.get("percent_covered")
            if pct is not None:
                out[_normalize(filename, root)] = float(pct) / 100.0
        return out
    if isinstance(data, dict):  # istanbul format
        for filename, info in data.items():
            if not isinstance(info, dict):
                continue
            statements = info.get("s")
            if isinstance(statements, dict) and statements:
                covered = sum(1 for v in statements.values() if v)
                out[_normalize(filename, root)] = covered / len(statements)
    return out


def infer_test_coverage_by_convention(
    all_files: Iterable[str], is_test: callable  # type: ignore[valid-type]
) -> dict[str, bool]:
    """Fallback when no coverage report exists: does a matching test file exist?

    Not a substitute for real coverage, but it distinguishes "definitely
    untested" from "probably tested" well enough to be useful, and BugSeer
    labels the rule accordingly so nobody mistakes it for measured coverage.
    """
    files = list(all_files)
    test_files = [f for f in files if is_test(f)]
    test_stems = set()
    for tf in test_files:
        stem = Path(tf).stem.lower()
        for prefix in ("test_", "test"):
            if stem.startswith(prefix):
                stem = stem[len(prefix):].lstrip("_")
        for suffix in ("_test", ".test", ".spec", "_spec", "spec"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)].rstrip("_.")
        if stem:
            test_stems.add(stem)

    result: dict[str, bool] = {}
    for f in files:
        if is_test(f):
            continue
        result[f] = Path(f).stem.lower() in test_stems
    return result
