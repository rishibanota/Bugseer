"""The rule engine: metrics in, explainable scored evidence out.

Design principle: every point of risk must be attributable. A rule never fires
without recording (a) what it measured, (b) the threshold it crossed, and
(c) where in the file to look. That record is what the CLI, the dashboard and
the `explain` command all render, and it is why a developer can audit the
score instead of trusting it.

Scores are additive and then squashed to 0-100 so that a file with eight
moderate problems and a file with one catastrophic one land in sensible places.
"""

from __future__ import annotations

import math
from typing import Any

from bugseer.config import Config
from bugseer.models import FileMetrics, GitMetrics, RuleHit


def _sev(score: float) -> str:
    if score >= 15:
        return "high"
    if score >= 7:
        return "medium"
    return "low"


def _scale(value: float, threshold: float, cap: float = 2.5) -> float:
    """How far past a threshold a measurement is, capped so one metric can't dominate."""
    if threshold <= 0:
        return 1.0
    return min(cap, value / threshold)


# ==========================================================================
# Phase 1: static rules
# ==========================================================================
def evaluate_static(metrics: FileMetrics, cfg: Config,
                    coverage: float | None = None,
                    has_test_file: bool | None = None,
                    cross_file_clones: list[dict] | None = None) -> list[RuleHit]:
    w = cfg.weights
    t = cfg.thresholds
    hits: list[RuleHit] = []

    # A file BugSeer could not read (binary, oversized, unreadable) carries no
    # usable evidence. Scoring it would be inventing a number, so stay silent.
    if not metrics.parse_ok and metrics.parse_error.startswith("skipped:"):
        return hits

    # Trivial files (empty, a lone constant, an __init__ re-export) cannot
    # meaningfully be "untested" or "complex". Flagging them buries the real
    # findings under noise, which is the fastest way to get a tool ignored.
    trivial = metrics.loc < 10 and metrics.function_count == 0 and metrics.class_count == 0

    # ---- long functions ---------------------------------------------------
    long_funcs = [f for f in metrics.long_functions if f.get("length", 0) >= t.long_function_lines]
    if long_funcs:
        worst = max(long_funcs, key=lambda f: f["length"])
        multiplier = _scale(worst["length"], t.long_function_lines, cap=3.0)
        score = w.long_function * multiplier
        hits.append(RuleHit(
            rule_id="long-function",
            title="Oversized function",
            score=score,
            detail=(
                f"{len(long_funcs)} function(s) exceed {t.long_function_lines} lines; "
                f"largest is `{worst['name']}` at {worst['length']} lines "
                f"(lines {worst['line']}-{worst.get('end_line', worst['line'])}). "
                "Long functions concentrate state and are the most common site of "
                "regressions during edits."
            ),
            phase="static",
            severity=_sev(score),
            locations=[
                {"line": f["line"], "end_line": f.get("end_line"), "name": f["name"],
                 "note": f"{f['length']} lines"}
                for f in long_funcs[:8]
            ],
            evidence={"threshold": t.long_function_lines, "max_length": worst["length"],
                      "count": len(long_funcs)},
        ))

    # ---- deep nesting / nested loops --------------------------------------
    if metrics.nested_loop_depth > t.nested_loop_depth or metrics.max_nesting_depth > t.max_nesting_depth + 2:
        depth = max(metrics.nested_loop_depth, metrics.max_nesting_depth - 2)
        multiplier = _scale(depth, t.nested_loop_depth, cap=2.5)
        score = w.deep_nesting * multiplier
        hits.append(RuleHit(
            rule_id="deep-nesting",
            title="Deeply nested control flow",
            score=score,
            detail=(
                f"Maximum loop nesting is {metrics.nested_loop_depth} and maximum block "
                f"depth is {metrics.max_nesting_depth} (threshold {t.nested_loop_depth}). "
                "Each extra level multiplies the number of execution paths a change can break."
            ),
            phase="static",
            severity=_sev(score),
            locations=[
                {"line": s["line"], "note": f"depth {s['depth']}"}
                for s in metrics.deep_nesting_sites[:8]
            ],
            evidence={"nested_loop_depth": metrics.nested_loop_depth,
                      "max_nesting_depth": metrics.max_nesting_depth,
                      "threshold": t.nested_loop_depth},
        ))

    # ---- too many branches ------------------------------------------------
    if metrics.branch_count > t.branch_count:
        multiplier = _scale(metrics.branch_count, t.branch_count, cap=2.5)
        score = w.high_branching * multiplier
        hits.append(RuleHit(
            rule_id="high-branching",
            title="High branch density",
            score=score,
            detail=(
                f"{metrics.branch_count} conditional branches (threshold {t.branch_count}). "
                "Dense branching means many untested combinations of state."
            ),
            phase="static",
            severity=_sev(score),
            evidence={"branch_count": metrics.branch_count, "threshold": t.branch_count},
        ))

    # ---- cyclomatic complexity --------------------------------------------
    if metrics.max_function_complexity > t.cyclomatic_complexity:
        worst = max(metrics.long_functions, key=lambda f: f.get("complexity", 0), default=None)
        multiplier = _scale(metrics.max_function_complexity, t.cyclomatic_complexity, cap=3.0)
        score = w.high_complexity * multiplier
        name = worst["name"] if worst else "?"
        line = worst["line"] if worst else 0
        hits.append(RuleHit(
            rule_id="high-complexity",
            title="High cyclomatic complexity",
            score=score,
            detail=(
                f"`{name}` has cyclomatic complexity {metrics.max_function_complexity} "
                f"(threshold {t.cyclomatic_complexity}); the file totals "
                f"{metrics.cyclomatic_complexity}. Complexity above ~20 correlates "
                "strongly with defect density and makes full test coverage impractical."
            ),
            phase="static",
            severity=_sev(score),
            locations=[{"line": line, "name": name,
                        "note": f"complexity {metrics.max_function_complexity}"}] if worst else [],
            evidence={"max_function_complexity": metrics.max_function_complexity,
                      "file_complexity": metrics.cyclomatic_complexity,
                      "cognitive_complexity": metrics.cognitive_complexity,
                      "threshold": t.cyclomatic_complexity},
        ))

    # ---- missing error handling -------------------------------------------
    if metrics.risky_calls >= 3 and metrics.try_blocks == 0:
        multiplier = _scale(metrics.risky_calls, 3, cap=2.0)
        score = w.no_error_handling * multiplier
        hits.append(RuleHit(
            rule_id="no-error-handling",
            title="No error handling around risky operations",
            score=score,
            detail=(
                f"{metrics.risky_calls} I/O, network, parsing or subprocess call(s) and "
                "zero try/catch blocks. Failures here surface as unhandled exceptions "
                "in production rather than degraded behaviour."
            ),
            phase="static",
            severity=_sev(score),
            evidence={"risky_calls": metrics.risky_calls, "try_blocks": 0},
        ))
    elif metrics.risky_calls >= 8 and metrics.try_blocks < metrics.risky_calls / 6:
        score = w.no_error_handling * 0.5
        hits.append(RuleHit(
            rule_id="sparse-error-handling",
            title="Sparse error handling",
            score=score,
            detail=(
                f"{metrics.risky_calls} risky call(s) guarded by only "
                f"{metrics.try_blocks} try block(s)."
            ),
            phase="static",
            severity=_sev(score),
            evidence={"risky_calls": metrics.risky_calls, "try_blocks": metrics.try_blocks},
        ))

    # ---- swallowed / bare exceptions --------------------------------------
    if metrics.bare_excepts or metrics.swallowed_exceptions:
        count = metrics.bare_excepts + metrics.swallowed_exceptions
        score = w.bare_except * min(2.0, count / 2 + 0.5)
        hits.append(RuleHit(
            rule_id="swallowed-exception",
            title="Swallowed or overly broad exception",
            score=score,
            detail=(
                f"{metrics.bare_excepts} bare handler(s) and "
                f"{metrics.swallowed_exceptions} empty handler(s). These hide the "
                "failure that caused a bug, turning a crash into silent corruption "
                "and making the eventual defect far harder to trace."
            ),
            phase="static",
            severity=_sev(score),
            evidence={"bare_excepts": metrics.bare_excepts,
                      "swallowed": metrics.swallowed_exceptions},
        ))

    # ---- global mutable state ---------------------------------------------
    if metrics.global_variables > 0:
        multiplier = _scale(metrics.global_variables, 2, cap=2.0)
        score = w.global_variables * multiplier
        names = ", ".join(metrics.global_names[:6]) or "unnamed"
        hits.append(RuleHit(
            rule_id="global-state",
            title="Mutable global state",
            score=score,
            detail=(
                f"{metrics.global_variables} global/module-level mutable binding(s): {names}. "
                "Shared mutable state creates action-at-a-distance bugs that unit tests "
                "rarely reproduce."
            ),
            phase="static",
            severity=_sev(score),
            evidence={"count": metrics.global_variables, "names": metrics.global_names[:12]},
        ))

    # ---- duplicate code ---------------------------------------------------
    dup_score = 0.0
    dup_detail: list[str] = []
    dup_evidence: dict[str, Any] = {}
    if metrics.duplicate_line_ratio >= t.duplicate_ratio:
        dup_score += w.duplicate_code * _scale(metrics.duplicate_line_ratio, t.duplicate_ratio, 2.0)
        dup_detail.append(
            f"{metrics.duplicate_line_ratio:.0%} of lines are internally duplicated "
            f"({metrics.duplicate_block_count} repeated block(s))"
        )
        dup_evidence["internal_ratio"] = round(metrics.duplicate_line_ratio, 4)
    if cross_file_clones:
        top = cross_file_clones[0]
        dup_score += w.duplicate_code * 0.6
        dup_detail.append(
            f"shares {top['shared_windows']} duplicated block(s) with `{top['partner']}`"
        )
        dup_evidence["clone_partners"] = [c["partner"] for c in cross_file_clones[:5]]
    if dup_score > 0:
        hits.append(RuleHit(
            rule_id="duplicate-code",
            title="Duplicated logic",
            score=dup_score,
            detail=(
                "; ".join(dup_detail).capitalize()
                + ". Copies drift apart: a fix applied to one copy leaves the others broken."
            ),
            phase="static",
            severity=_sev(dup_score),
            locations=[
                {"line": ln, "note": f"clone of {c['partner']}"}
                for c in (cross_file_clones or [])[:3] for ln in c["lines"][:2]
            ],
            evidence=dup_evidence,
        ))

    # ---- test coverage ----------------------------------------------------
    if coverage is not None:
        if coverage < t.low_coverage:
            deficit = (t.low_coverage - coverage) / max(0.01, t.low_coverage)
            score = w.low_test_coverage * min(1.6, 0.4 + deficit * 1.2)
            hits.append(RuleHit(
                rule_id="low-coverage",
                title="Low measured test coverage",
                score=score,
                detail=(
                    f"Measured line coverage is {coverage:.0%} (threshold "
                    f"{t.low_coverage:.0%}). Changes to uncovered code reach production "
                    "without any automated check."
                ),
                phase="static",
                severity=_sev(score),
                evidence={"coverage": round(coverage, 4), "threshold": t.low_coverage,
                          "source": "coverage report"},
            ))
    elif has_test_file is False and not trivial:
        score = w.low_test_coverage * 0.55
        hits.append(RuleHit(
            rule_id="no-test-file",
            title="No matching test file found",
            score=score,
            detail=(
                "No test file matching this module's name was found. This is a naming "
                "heuristic, not measured coverage - point BugSeer at a coverage report "
                "(BUGSEER_COVERAGE_FILE) for an exact figure."
            ),
            phase="static",
            severity=_sev(score),
            evidence={"source": "naming heuristic"},
        ))

    # ---- oversized file ---------------------------------------------------
    if metrics.loc > t.god_file_loc:
        multiplier = _scale(metrics.loc, t.god_file_loc, cap=2.5)
        score = w.god_file * multiplier
        hits.append(RuleHit(
            rule_id="god-file",
            title="Oversized file",
            score=score,
            detail=(
                f"{metrics.loc} lines of code across {metrics.function_count} function(s) "
                f"and {metrics.class_count} class(es) (threshold {t.god_file_loc}). "
                "Large files attract unrelated changes and merge conflicts."
            ),
            phase="static",
            severity=_sev(score),
            evidence={"loc": metrics.loc, "threshold": t.god_file_loc},
        ))

    # ---- long parameter lists ---------------------------------------------
    if metrics.max_parameters > t.max_parameters:
        score = w.long_parameter_list * _scale(metrics.max_parameters, t.max_parameters, 2.0)
        worst = max(metrics.long_functions, key=lambda f: f.get("params", 0), default=None)
        hits.append(RuleHit(
            rule_id="long-parameter-list",
            title="Long parameter list",
            score=score,
            detail=(
                f"Up to {metrics.max_parameters} parameters (threshold {t.max_parameters})"
                + (f", in `{worst['name']}`" if worst else "")
                + ". Positional arguments are easy to transpose at call sites."
            ),
            phase="static",
            severity=_sev(score),
            locations=[{"line": worst["line"], "name": worst["name"]}] if worst else [],
            evidence={"max_parameters": metrics.max_parameters},
        ))

    # ---- mutable default arguments ----------------------------------------
    if metrics.mutable_default_args:
        score = w.mutable_default_arg * min(2.0, metrics.mutable_default_args)
        hits.append(RuleHit(
            rule_id="mutable-default-arg",
            title="Mutable default argument",
            score=score,
            detail=(
                f"{metrics.mutable_default_args} function(s) use a mutable default value. "
                "The default is created once and shared across every call, which produces "
                "state that leaks between invocations."
            ),
            phase="static",
            severity=_sev(score),
            evidence={"count": metrics.mutable_default_args},
        ))

    # ---- magic numbers ----------------------------------------------------
    if metrics.magic_numbers > t.magic_number_count:
        score = w.magic_numbers * _scale(metrics.magic_numbers, t.magic_number_count, 2.0)
        hits.append(RuleHit(
            rule_id="magic-numbers",
            title="Unexplained numeric literals",
            score=score,
            detail=(
                f"{metrics.magic_numbers} unnamed numeric literal(s). Values duplicated "
                "across a file get updated inconsistently."
            ),
            phase="static",
            severity=_sev(score),
            evidence={"count": metrics.magic_numbers, "threshold": t.magic_number_count},
        ))

    # ---- TODO debt --------------------------------------------------------
    if metrics.todo_comments >= t.todo_count:
        score = w.todo_debt * _scale(metrics.todo_comments, t.todo_count, 2.0)
        hits.append(RuleHit(
            rule_id="todo-debt",
            title="Accumulated TODO/FIXME markers",
            score=score,
            detail=(
                f"{metrics.todo_comments} TODO/FIXME/HACK marker(s) - the authors "
                "themselves flagged unfinished behaviour here."
            ),
            phase="static",
            severity=_sev(score),
            evidence={"count": metrics.todo_comments},
        ))

    # ---- undocumented complexity ------------------------------------------
    if metrics.loc > 150 and metrics.comment_ratio < t.low_comment_ratio and metrics.cyclomatic_complexity > 15:
        score = w.low_comment_ratio
        hits.append(RuleHit(
            rule_id="undocumented-complexity",
            title="Complex but undocumented",
            score=score,
            detail=(
                f"Complexity {metrics.cyclomatic_complexity} with only "
                f"{metrics.comment_ratio:.1%} comment lines. Non-obvious logic without "
                "explanation invites incorrect edits."
            ),
            phase="static",
            severity="low",
            evidence={"comment_ratio": round(metrics.comment_ratio, 4)},
        ))

    return hits


# ==========================================================================
# Phase 2: git rules
# ==========================================================================
def evaluate_git(git: GitMetrics, cfg: Config) -> list[RuleHit]:
    w = cfg.weights
    t = cfg.thresholds
    hits: list[RuleHit] = []

    if git.commit_count == 0:
        return hits

    # ---- change frequency -------------------------------------------------
    if git.commit_count >= t.commit_count:
        multiplier = _scale(git.commit_count, t.commit_count, cap=2.2)
        score = w.change_frequency * multiplier
        hits.append(RuleHit(
            rule_id="change-frequency",
            title="Frequently modified file",
            score=score,
            detail=(
                f"{git.commit_count} commits in the analysed window "
                f"({git.recent_commits_90d} in the last 90 days). Change frequency is one "
                "of the strongest empirical predictors of future defects."
            ),
            phase="git",
            severity=_sev(score),
            evidence={"commit_count": git.commit_count,
                      "recent_90d": git.recent_commits_90d,
                      "threshold": t.commit_count},
        ))

    # ---- bug-fix density --------------------------------------------------
    if git.bugfix_commit_count >= 2 and git.bugfix_ratio >= t.bugfix_ratio:
        multiplier = _scale(git.bugfix_ratio, t.bugfix_ratio, cap=2.2)
        score = w.bugfix_density * multiplier
        hits.append(RuleHit(
            rule_id="bugfix-density",
            title="High bug-fix density",
            score=score,
            detail=(
                f"{git.bugfix_commit_count} of {git.commit_count} commits "
                f"({git.bugfix_ratio:.0%}) look like bug fixes. Code that has needed "
                "repeated repair tends to need more."
            ),
            phase="git",
            severity=_sev(score),
            evidence={"bugfix_commits": git.bugfix_commit_count,
                      "ratio": git.bugfix_ratio,
                      "last_subject": git.last_commit_subject},
        ))

    # ---- reverts ----------------------------------------------------------
    if git.revert_count >= t.revert_count:
        score = w.revert_history * min(2.0, git.revert_count)
        hits.append(RuleHit(
            rule_id="revert-history",
            title="Previously reverted",
            score=score,
            detail=(
                f"{git.revert_count} revert/rollback commit(s) touched this file. "
                "A revert is direct evidence that a change here broke something in production."
            ),
            phase="git",
            severity=_sev(score),
            evidence={"revert_count": git.revert_count},
        ))

    # ---- fix-follow rate --------------------------------------------------
    if git.fix_follow_rate >= t.fix_follow_rate and git.commit_count >= 5:
        multiplier = _scale(git.fix_follow_rate, t.fix_follow_rate, cap=2.0)
        score = w.fix_follow_rate * multiplier
        hits.append(RuleHit(
            rule_id="fix-follow-rate",
            title="Edits here usually need follow-up fixes",
            score=score,
            detail=(
                f"{git.fix_follow_rate:.0%} of non-fix commits to this file were followed "
                "by a bug-fix commit within 7 days. Changes here rarely land cleanly the "
                "first time."
            ),
            phase="git",
            severity=_sev(score),
            evidence={"fix_follow_rate": git.fix_follow_rate},
        ))

    # ---- recent churn -----------------------------------------------------
    if git.recent_commits_30d >= t.recent_commits_30d:
        score = w.recent_churn * _scale(git.recent_commits_30d, t.recent_commits_30d, 2.0)
        hits.append(RuleHit(
            rule_id="recent-churn",
            title="Actively churning right now",
            score=score,
            detail=(
                f"{git.recent_commits_30d} commits in the last 30 days "
                f"(+{git.lines_added}/-{git.lines_deleted} lines overall). "
                "Recently modified code has not yet been exercised in production."
            ),
            phase="git",
            severity=_sev(score),
            evidence={"recent_30d": git.recent_commits_30d,
                      "days_since_last_change": git.days_since_last_change},
        ))

    # ---- many authors -----------------------------------------------------
    if git.author_count >= t.author_count:
        score = w.many_authors * _scale(git.author_count, t.author_count, 2.0)
        hits.append(RuleHit(
            rule_id="many-authors",
            title="Many contributors",
            score=score,
            detail=(
                f"{git.author_count} different authors have modified this file. "
                "Divergent mental models of the same code produce integration defects."
            ),
            phase="git",
            severity=_sev(score),
            evidence={"author_count": git.author_count, "authors": git.authors[:8]},
        ))

    # ---- knowledge silo ---------------------------------------------------
    elif git.ownership_ratio >= t.ownership_ratio and git.commit_count >= 10:
        score = w.knowledge_silo
        hits.append(RuleHit(
            rule_id="knowledge-silo",
            title="Single-owner file",
            score=score,
            detail=(
                f"{git.ownership_ratio:.0%} of commits come from one author "
                f"({git.authors[0] if git.authors else 'unknown'}). Bus-factor risk: "
                "nobody else has context when it breaks."
            ),
            phase="git",
            severity="low",
            evidence={"ownership_ratio": git.ownership_ratio},
        ))

    return hits


# ==========================================================================
# Phase 5 inputs: dependency-graph rules
# ==========================================================================
def evaluate_graph(path: str, dependents: list[str], centrality: float,
                   cfg: Config) -> list[RuleHit]:
    w = cfg.weights
    t = cfg.thresholds
    hits: list[RuleHit] = []

    if len(dependents) >= t.fan_in:
        score = w.high_fan_in * _scale(len(dependents), t.fan_in, 2.0)
        hits.append(RuleHit(
            rule_id="high-fan-in",
            title="Many modules depend on this file",
            score=score,
            detail=(
                f"{len(dependents)} module(s) import this file. A defect here propagates "
                "to every one of them, so the blast radius of a mistake is large."
            ),
            phase="graph",
            severity=_sev(score),
            evidence={"fan_in": len(dependents), "sample": dependents[:10]},
        ))

    if centrality >= 0.25:
        score = w.high_centrality * min(2.0, centrality * 3)
        hits.append(RuleHit(
            rule_id="high-centrality",
            title="Structurally central module",
            score=score,
            detail=(
                f"Betweenness centrality {centrality:.2f}: this file sits on many paths "
                "through the dependency graph, so changes ripple widely."
            ),
            phase="graph",
            severity=_sev(score),
            evidence={"centrality": round(centrality, 4)},
        ))

    return hits


# ==========================================================================
# Aggregation
# ==========================================================================
def squash(raw: float, midpoint: float = 55.0) -> float:
    """Map an unbounded additive score onto 0-100.

    A saturating curve keeps the top of the range meaningful: a file with 300
    raw points is worse than one with 150, but both are unambiguously critical
    and the difference should not compress everything else into the low end.
    """
    if raw <= 0:
        return 0.0
    return round(100.0 * (1.0 - math.exp(-raw / midpoint)), 2)


def band_for(score: float, cfg: Config) -> str:
    t = cfg.thresholds
    if score >= t.band_critical:
        return "critical"
    if score >= t.band_high:
        return "high"
    if score >= t.band_medium:
        return "medium"
    return "low"
