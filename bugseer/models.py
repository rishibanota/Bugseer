"""Core data structures shared by every BugSeer phase.

These are plain dataclasses (not pydantic models) so the analysis engine has
zero import cost and can run in worker subprocesses cheaply. Pydantic is only
used at the API boundary in `bugseer.server`.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any


def _round(value: float, digits: int = 2) -> float:
    return round(float(value), digits)


@dataclass
class RuleHit:
    """A single rule that fired on a file, with the evidence that triggered it.

    Every risk point BugSeer reports traces back to one of these, which is what
    makes the final score explainable rather than a black box.
    """

    rule_id: str
    title: str
    score: float
    detail: str
    phase: str = "static"          # static | git | ml | graph
    severity: str = "medium"       # low | medium | high
    locations: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["score"] = _round(self.score)
        return d


@dataclass
class FileMetrics:
    """Raw measurements for one file. Feeds both the rules and the ML model."""

    path: str
    language: str = "unknown"
    loc: int = 0                      # non-blank, non-comment lines
    total_lines: int = 0
    blank_lines: int = 0
    comment_lines: int = 0
    function_count: int = 0
    class_count: int = 0
    max_function_length: int = 0
    avg_function_length: float = 0.0
    long_functions: list[dict[str, Any]] = field(default_factory=list)
    max_nesting_depth: int = 0
    deep_nesting_sites: list[dict[str, Any]] = field(default_factory=list)
    cyclomatic_complexity: int = 0
    max_function_complexity: int = 0
    cognitive_complexity: int = 0
    branch_count: int = 0
    loop_count: int = 0
    nested_loop_depth: int = 0
    try_blocks: int = 0
    except_handlers: int = 0
    bare_excepts: int = 0
    swallowed_exceptions: int = 0
    risky_calls: int = 0              # I/O, network, subprocess, db, etc.
    global_variables: int = 0
    global_names: list[str] = field(default_factory=list)
    mutable_default_args: int = 0
    magic_numbers: int = 0
    todo_comments: int = 0
    max_parameters: int = 0
    duplicate_block_count: int = 0
    duplicate_line_ratio: float = 0.0
    imports: list[str] = field(default_factory=list)
    internal_deps: list[str] = field(default_factory=list)
    comment_ratio: float = 0.0
    parse_ok: bool = True
    parser: str = "heuristic"         # ast | tree-sitter | heuristic
    parse_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class GitMetrics:
    """Everything BugSeer learns from `git log` for one file (Phase 2)."""

    path: str
    commit_count: int = 0
    bugfix_commit_count: int = 0
    revert_count: int = 0
    author_count: int = 0
    authors: list[str] = field(default_factory=list)
    lines_added: int = 0
    lines_deleted: int = 0
    churn: int = 0
    days_since_last_change: float = 999.0
    days_since_created: float = 999.0
    recent_commits_30d: int = 0
    recent_commits_90d: int = 0
    bugfix_ratio: float = 0.0
    ownership_ratio: float = 1.0      # share of commits by the top author
    fix_follow_rate: float = 0.0      # edits followed by a bug fix within 7 days
    co_change_partners: list[dict[str, Any]] = field(default_factory=list)
    last_commit_hash: str = ""
    last_commit_subject: str = ""
    tracked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class FileRisk:
    """The final per-file verdict that the CLI, dashboard and API all render."""

    path: str
    language: str = "unknown"
    score: float = 0.0                # 0-100, capped
    raw_score: float = 0.0            # uncapped sum, useful for debugging
    band: str = "low"                 # low | medium | high | critical
    static_score: float = 0.0
    git_score: float = 0.0
    graph_score: float = 0.0
    ml_probability: float | None = None
    ml_contributions: list[dict[str, Any]] = field(default_factory=list)
    hits: list[RuleHit] = field(default_factory=list)
    metrics: FileMetrics | None = None
    git: GitMetrics | None = None
    coverage: float | None = None
    dependents: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    centrality: float = 0.0

    @property
    def emoji(self) -> str:
        return {"low": "🟢", "medium": "🟡", "high": "🔴", "critical": "🔥"}[self.band]

    def top_reasons(self, limit: int = 5) -> list[RuleHit]:
        return sorted(self.hits, key=lambda h: h.score, reverse=True)[:limit]

    def to_dict(self, *, include_metrics: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {
            "path": self.path,
            "language": self.language,
            "score": _round(self.score),
            "raw_score": _round(self.raw_score),
            "band": self.band,
            "emoji": self.emoji,
            "static_score": _round(self.static_score),
            "git_score": _round(self.git_score),
            "graph_score": _round(self.graph_score),
            "ml_probability": (
                _round(self.ml_probability, 4) if self.ml_probability is not None else None
            ),
            "ml_contributions": self.ml_contributions,
            "hits": [h.to_dict() for h in self.hits],
            "coverage": self.coverage,
            "dependents": self.dependents,
            "dependencies": self.dependencies,
            "centrality": _round(self.centrality, 4),
        }
        if include_metrics:
            d["metrics"] = self.metrics.to_dict() if self.metrics else None
            d["git"] = self.git.to_dict() if self.git else None
        return d


@dataclass
class RepoReport:
    """A complete scan of a repository."""

    root: str
    generated_at: str
    files: list[FileRisk] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    hotspots: list[dict[str, Any]] = field(default_factory=list)
    git_available: bool = False
    ml_used: bool = False
    duration_seconds: float = 0.0
    version: str = "0.1.0"

    def by_path(self, path: str) -> FileRisk | None:
        for f in self.files:
            if f.path == path:
                return f
        return None

    def to_dict(self, *, include_metrics: bool = True) -> dict[str, Any]:
        return {
            "root": self.root,
            "generated_at": self.generated_at,
            "version": self.version,
            "git_available": self.git_available,
            "ml_used": self.ml_used,
            "duration_seconds": _round(self.duration_seconds, 3),
            "config": self.config,
            "summary": self.summary,
            "hotspots": self.hotspots,
            "files": [f.to_dict(include_metrics=include_metrics) for f in self.files],
        }


@dataclass
class ImpactResult:
    """Phase 5: predicted blast radius of changing one or more files."""

    seeds: list[str]
    affected: list[dict[str, Any]] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seeds": self.seeds,
            "affected": self.affected,
            "explanation": self.explanation,
        }
