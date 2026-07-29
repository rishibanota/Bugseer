"""Phase 3: learn from this repository's own past bugs.

Everything here runs locally on the user's machine. No data leaves the process.

**How labels are derived (no bug tracker required).** BugSeer replays the git
history: a file is a positive example if it was touched by a bug-fix commit in
the *label window* (the most recent N days), and the features are computed from
the history *before* that window. That temporal split is what makes the model
predictive rather than circular - it is literally asking "given how this file
looked six months ago, did it need fixing since?".

The default estimator is scikit-learn's `GradientBoostingClassifier`
(always available). XGBoost or LightGBM are used automatically if installed.

Explainability is a first-class requirement here: every prediction ships with
per-feature contributions so the dashboard can say *why* the model is worried,
not just how worried it is.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from bugseer.git_intel import GitAnalyzer, parse_log
from bugseer.models import FileMetrics, GitMetrics

FEATURE_NAMES: list[str] = [
    "loc",
    "function_count",
    "max_function_length",
    "max_nesting_depth",
    "cyclomatic_complexity",
    "max_function_complexity",
    "cognitive_complexity",
    "branch_count",
    "loop_count",
    "try_blocks",
    "risky_calls_per_100loc",
    "global_variables",
    "duplicate_line_ratio",
    "comment_ratio",
    "max_parameters",
    "dependency_count",
    "dependent_count",
    "commit_count",
    "bugfix_commit_count",
    "bugfix_ratio",
    "revert_count",
    "author_count",
    "ownership_ratio",
    "churn_per_commit",
    "days_since_last_change",
    "age_days",
    "recent_commits_90d",
    "fix_follow_rate",
]

FEATURE_LABELS: dict[str, str] = {
    "loc": "file size (lines of code)",
    "function_count": "number of functions",
    "max_function_length": "longest function",
    "max_nesting_depth": "deepest nesting",
    "cyclomatic_complexity": "total complexity",
    "max_function_complexity": "most complex function",
    "cognitive_complexity": "cognitive complexity",
    "branch_count": "branch count",
    "loop_count": "loop count",
    "try_blocks": "error handling blocks",
    "risky_calls_per_100loc": "risky call density",
    "global_variables": "global variables",
    "duplicate_line_ratio": "duplicated lines",
    "comment_ratio": "comment ratio",
    "max_parameters": "largest parameter list",
    "dependency_count": "outgoing dependencies",
    "dependent_count": "incoming dependencies",
    "commit_count": "commit count",
    "bugfix_commit_count": "past bug-fix commits",
    "bugfix_ratio": "bug-fix ratio",
    "revert_count": "reverts",
    "author_count": "contributor count",
    "ownership_ratio": "ownership concentration",
    "churn_per_commit": "average churn per commit",
    "days_since_last_change": "time since last change",
    "age_days": "file age",
    "recent_commits_90d": "commits in last 90 days",
    "fix_follow_rate": "edits needing follow-up fixes",
}


def build_feature_vector(
    metrics: FileMetrics,
    git: GitMetrics,
    dependency_count: int = 0,
    dependent_count: int = 0,
) -> list[float]:
    loc = max(1, metrics.loc)
    return [
        float(metrics.loc),
        float(metrics.function_count),
        float(metrics.max_function_length),
        float(metrics.max_nesting_depth),
        float(metrics.cyclomatic_complexity),
        float(metrics.max_function_complexity),
        float(metrics.cognitive_complexity),
        float(metrics.branch_count),
        float(metrics.loop_count),
        float(metrics.try_blocks),
        float(metrics.risky_calls) * 100.0 / loc,
        float(metrics.global_variables),
        float(metrics.duplicate_line_ratio),
        float(metrics.comment_ratio),
        float(metrics.max_parameters),
        float(dependency_count),
        float(dependent_count),
        float(git.commit_count),
        float(git.bugfix_commit_count),
        float(git.bugfix_ratio),
        float(git.revert_count),
        float(git.author_count),
        float(git.ownership_ratio),
        float(git.churn) / max(1.0, git.commit_count),
        float(min(git.days_since_last_change, 2000.0)),
        float(min(git.days_since_created, 5000.0)),
        float(git.recent_commits_90d),
        float(git.fix_follow_rate),
    ]


def _clamp_probability(p: float, floor: float = 0.02, ceiling: float = 0.95) -> float:
    """Keep reported probabilities away from 0% and 100%.

    A model fitted on a few dozen files from one repository cannot justify
    absolute certainty, and "100% chance of a bug" is the kind of claim that
    destroys a developer's trust the first time it is wrong. Clamping keeps the
    ranking identical while making the number defensible.
    """
    return max(floor, min(ceiling, float(p)))


@dataclass
class TrainingReport:
    trained: bool = False
    reason: str = ""
    samples: int = 0
    positives: int = 0
    features: int = len(FEATURE_NAMES)
    estimator: str = ""
    auc: float | None = None
    accuracy: float | None = None
    precision: float | None = None
    recall: float | None = None
    baseline_rate: float | None = None
    top_features: list[dict[str, Any]] = field(default_factory=list)
    label_window_days: int = 0
    trained_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "trained": self.trained,
            "reason": self.reason,
            "samples": self.samples,
            "positives": self.positives,
            "features": self.features,
            "estimator": self.estimator,
            "auc": self.auc,
            "accuracy": self.accuracy,
            "precision": self.precision,
            "recall": self.recall,
            "baseline_rate": self.baseline_rate,
            "top_features": self.top_features,
            "label_window_days": self.label_window_days,
            "trained_at": self.trained_at,
        }


class BugPredictor:
    """A small, local, explainable defect-probability model."""

    def __init__(self, model_dir: Path) -> None:
        self.model_dir = Path(model_dir)
        self.model = None
        self.scaler = None
        self.report = TrainingReport()
        self._baseline: float = 0.15
        # Out-of-fold predictions for files the model was trained on. A tree
        # ensemble memorises its training set, so asking it about a file it has
        # already seen yields ~100% and is meaningless. For those files we serve
        # the cross-validated estimate instead - what the model would have said
        # if it had never seen them.
        self._oof: dict[str, float] = {}

    # ------------------------------------------------------------------
    @property
    def model_path(self) -> Path:
        return self.model_dir / "model.joblib"

    @property
    def meta_path(self) -> Path:
        return self.model_dir / "model_meta.json"

    def available(self) -> bool:
        return self.model is not None

    # ------------------------------------------------------------------
    def load(self) -> bool:
        if not self.model_path.is_file():
            return False
        try:
            import joblib
            payload = joblib.load(self.model_path)
            self.model = payload["model"]
            self.scaler = payload.get("scaler")
            self._baseline = payload.get("baseline", 0.15)
            self._oof = payload.get("oof", {}) or {}
            if self.meta_path.is_file():
                meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
                self.report = TrainingReport(**{
                    k: v for k, v in meta.items()
                    if k in TrainingReport.__dataclass_fields__
                })
            return True
        except Exception:  # noqa: BLE001 - a stale/corrupt model must not break scanning
            self.model = None
            return False

    def save(self) -> None:
        self.model_dir.mkdir(parents=True, exist_ok=True)
        import joblib
        joblib.dump(
            {"model": self.model, "scaler": self.scaler,
             "features": FEATURE_NAMES, "baseline": self._baseline,
             "oof": self._oof},
            self.model_path,
        )
        self.meta_path.write_text(
            json.dumps(self.report.to_dict(), indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    def _make_estimator(self):
        """Prefer a gradient booster if one is installed; always fall back to sklearn."""
        try:
            import lightgbm as lgb
            return lgb.LGBMClassifier(
                n_estimators=250, learning_rate=0.05, num_leaves=15,
                min_child_samples=5, subsample=0.9, colsample_bytree=0.9,
                verbose=-1, random_state=42,
            ), "lightgbm.LGBMClassifier"
        except ModuleNotFoundError:
            pass
        try:
            import xgboost as xgb
            return xgb.XGBClassifier(
                n_estimators=250, learning_rate=0.05, max_depth=4,
                subsample=0.9, colsample_bytree=0.9, eval_metric="logloss",
                random_state=42,
            ), "xgboost.XGBClassifier"
        except ModuleNotFoundError:
            pass
        from sklearn.ensemble import GradientBoostingClassifier
        return GradientBoostingClassifier(
            n_estimators=200, learning_rate=0.06, max_depth=3,
            subsample=0.9, random_state=42,
        ), "sklearn.GradientBoostingClassifier"

    # ------------------------------------------------------------------
    def train(
        self,
        root: Path,
        metrics_by_path: dict[str, FileMetrics],
        graph_degrees: dict[str, tuple[int, int]],
        *,
        label_window_days: int = 180,
        history_days: int = 1460,
        min_samples: int = 40,
        min_positives: int = 8,
    ) -> TrainingReport:
        """Train on this repo's history using a temporal train/label split."""
        started = time.time()
        report = TrainingReport(label_window_days=label_window_days)

        commits = parse_log(root, history_days=history_days)
        if not commits:
            report.reason = "No git history available to learn from."
            self.report = report
            return report

        now = datetime.now(timezone.utc).timestamp()
        cutoff = now - label_window_days * 86400

        # --- labels: was the file bug-fixed inside the recent window? ------
        from bugseer.git_intel import BUGFIX_PATTERN, NON_FIX_PREFIX

        def is_fix(subject: str) -> bool:
            if NON_FIX_PREFIX.match(subject):
                return False
            return bool(BUGFIX_PATTERN.search(subject))

        labels: dict[str, int] = {}
        for commit in commits:
            if commit.timestamp >= cutoff and is_fix(commit.subject):
                for path in commit.files:
                    labels[path] = 1

        # --- features: history strictly BEFORE the label window ------------
        past = [c for c in commits if c.timestamp < cutoff]
        if not past:
            report.reason = (
                f"All history falls inside the {label_window_days}-day label window; "
                "no earlier period to learn from."
            )
            self.report = report
            return report

        analyzer = GitAnalyzer(root, history_days=0)
        analyzer.available = True
        analyzer.commits = past
        # Re-run the aggregation over the truncated commit list.
        past_metrics = self._aggregate_past(past, cutoff)

        rows: list[list[float]] = []
        targets: list[int] = []
        paths: list[str] = []
        for path, metrics in metrics_by_path.items():
            gm = past_metrics.get(path)
            if gm is None or gm.commit_count == 0:
                continue  # file did not exist before the label window
            deps, dependents = graph_degrees.get(path, (0, 0))
            rows.append(build_feature_vector(metrics, gm, deps, dependents))
            targets.append(1 if labels.get(path) else 0)
            paths.append(path)

        report.samples = len(rows)
        report.positives = int(sum(targets))

        if len(rows) < min_samples:
            report.reason = (
                f"Only {len(rows)} usable sample(s); need at least {min_samples}. "
                "BugSeer will keep using rule-based scoring."
            )
            self.report = report
            return report
        if report.positives < min_positives:
            report.reason = (
                f"Only {report.positives} file(s) received a bug fix in the last "
                f"{label_window_days} days; need at least {min_positives} to learn a "
                "meaningful signal. Try a longer --label-window."
            )
            self.report = report
            return report
        if report.positives == len(rows):
            report.reason = "Every file was bug-fixed in the window; no negative class."
            self.report = report
            return report

        X = np.array(rows, dtype=float)
        y = np.array(targets, dtype=int)
        self._baseline = float(y.mean())

        from sklearn.model_selection import StratifiedKFold, cross_val_predict
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import roc_auc_score, accuracy_score, precision_score, recall_score

        self.scaler = StandardScaler().fit(X)
        Xs = self.scaler.transform(X)
        estimator, name = self._make_estimator()
        report.estimator = name

        # Honest out-of-fold estimates rather than training-set scores.
        n_splits = max(2, min(5, report.positives, len(y) - report.positives))
        try:
            cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
            proba = cross_val_predict(estimator, Xs, y, cv=cv, method="predict_proba")[:, 1]
            preds = (proba >= 0.5).astype(int)
            report.auc = round(float(roc_auc_score(y, proba)), 4)
            report.accuracy = round(float(accuracy_score(y, preds)), 4)
            report.precision = round(float(precision_score(y, preds, zero_division=0)), 4)
            report.recall = round(float(recall_score(y, preds, zero_division=0)), 4)
            # Keep the honest, never-seen-this-file estimate for each training file.
            self._oof = {path: float(p) for path, p in zip(paths, proba)}
        except Exception:  # noqa: BLE001 - metrics are informative, not essential
            pass

        estimator.fit(Xs, y)
        self.model = estimator
        report.baseline_rate = round(self._baseline, 4)
        report.trained = True
        report.trained_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        report.reason = (
            f"Trained on {report.samples} files ({report.positives} bug-fixed) in "
            f"{time.time() - started:.1f}s."
        )

        importances = getattr(estimator, "feature_importances_", None)
        if importances is not None:
            order = np.argsort(importances)[::-1][:10]
            total = float(np.sum(importances)) or 1.0
            report.top_features = [
                {
                    "feature": FEATURE_NAMES[i],
                    "label": FEATURE_LABELS.get(FEATURE_NAMES[i], FEATURE_NAMES[i]),
                    "importance": round(float(importances[i]) / total, 4),
                }
                for i in order if importances[i] > 0
            ]

        self.report = report
        return report

    @staticmethod
    def _aggregate_past(commits: list, cutoff: float) -> dict[str, GitMetrics]:
        """Aggregate git metrics from a commit slice (used for training features)."""
        from collections import Counter, defaultdict
        from bugseer.git_intel import BUGFIX_PATTERN, NON_FIX_PREFIX, REVERT_PATTERN

        def is_fix(subject: str) -> bool:
            if NON_FIX_PREFIX.match(subject):
                return False
            return bool(BUGFIX_PATTERN.search(subject))

        stats: dict[str, dict] = defaultdict(
            lambda: {"commits": 0, "bugfix": 0, "reverts": 0, "adds": 0, "dels": 0,
                     "authors": Counter(), "ts": []}
        )
        for commit in commits:
            fix = is_fix(commit.subject)
            revert = bool(REVERT_PATTERN.match(commit.subject))
            for path in commit.files:
                s = stats[path]
                s["commits"] += 1
                s["adds"] += commit.insertions.get(path, 0)
                s["dels"] += commit.deletions.get(path, 0)
                s["authors"][commit.author] += 1
                s["ts"].append(commit.timestamp)
                if fix:
                    s["bugfix"] += 1
                if revert:
                    s["reverts"] += 1

        out: dict[str, GitMetrics] = {}
        for path, s in stats.items():
            ts = sorted(s["ts"])
            authors: Counter = s["authors"]
            top = authors.most_common(1)[0][1] if authors else 0
            out[path] = GitMetrics(
                path=path,
                commit_count=s["commits"],
                bugfix_commit_count=s["bugfix"],
                revert_count=s["reverts"],
                author_count=len(authors),
                authors=[a for a, _ in authors.most_common(5)],
                lines_added=s["adds"],
                lines_deleted=s["dels"],
                churn=s["adds"] + s["dels"],
                days_since_last_change=round((cutoff - ts[-1]) / 86400, 2) if ts else 999.0,
                days_since_created=round((cutoff - ts[0]) / 86400, 2) if ts else 999.0,
                recent_commits_90d=sum(1 for t in ts if cutoff - t <= 90 * 86400),
                bugfix_ratio=round(s["bugfix"] / max(1, s["commits"]), 4),
                ownership_ratio=round(top / max(1, s["commits"]), 4),
            )
        return out

    # ------------------------------------------------------------------
    def predict(self, features: list[float], path: str | None = None) -> float | None:
        """Probability that this file needs a bug fix in the label window.

        For a file that was part of training we return the out-of-fold estimate,
        because the fitted ensemble has effectively memorised those rows and
        would report a near-certain 100%. Reporting a memorised value as a
        prediction would be misleading, and this tool's whole premise is that a
        developer can trust the numbers it shows.
        """
        if self.model is None:
            return None
        if path is not None and path in self._oof:
            return _clamp_probability(self._oof[path])
        X = np.array([features], dtype=float)
        if self.scaler is not None:
            X = self.scaler.transform(X)
        try:
            return _clamp_probability(float(self.model.predict_proba(X)[0, 1]))
        except Exception:  # noqa: BLE001
            return None

    def is_out_of_fold(self, path: str) -> bool:
        """True when `path`'s probability came from cross-validation."""
        return path in self._oof

    def explain(self, features: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        """Per-feature contributions for one prediction.

        Uses the model's own importances weighted by how unusual this file's
        value is (z-score from the training scaler). This is a fast, honest
        approximation of a SHAP-style attribution: it says which inputs pushed
        this particular file away from the norm, in the direction the model
        cares about, without adding a heavy dependency.
        """
        if self.model is None or self.scaler is None:
            return []
        importances = getattr(self.model, "feature_importances_", None)
        if importances is None:
            return []

        X = np.array([features], dtype=float)
        z = self.scaler.transform(X)[0]
        contributions = importances * z
        order = np.argsort(np.abs(contributions))[::-1][:top_k]

        out: list[dict[str, Any]] = []
        for i in order:
            if abs(contributions[i]) < 1e-6:
                continue
            name = FEATURE_NAMES[i]
            raw = features[i]
            out.append({
                "feature": name,
                "label": FEATURE_LABELS.get(name, name),
                "value": round(float(raw), 3),
                "z_score": round(float(z[i]), 2),
                "contribution": round(float(contributions[i]), 4),
                "direction": "increases" if contributions[i] > 0 else "decreases",
            })
        return out

    def risk_points(self, probability: float, weight: float = 30.0) -> float:
        """Convert a probability into additive risk points, relative to baseline.

        A file predicted at the repository's base rate contributes nothing; the
        model only moves a score when it actually disagrees with the average.
        """
        baseline = max(0.01, min(0.9, self._baseline))
        lift = (probability - baseline) / max(0.05, 1.0 - baseline)
        return max(0.0, weight * lift * (1.0 + 0.5 * math.tanh(lift)))
