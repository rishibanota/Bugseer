"""Configuration loading and the tunable rule weights.

Precedence (highest wins):
    CLI flags  >  .bugseer.toml  >  environment / .env  >  defaults
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


# --------------------------------------------------------------------------
# Optional .env support. Never required.
# --------------------------------------------------------------------------
def load_dotenv_if_present(root: Path | None = None) -> bool:
    """Load a .env file if python-dotenv is installed and a .env exists.

    Returns True if a file was loaded. Absence is completely fine: BugSeer's
    entire analysis pipeline runs without any environment configuration.
    """
    root = Path(root or Path.cwd())
    env_path = root / ".env"
    if not env_path.is_file():
        return False
    try:
        from dotenv import load_dotenv  # type: ignore
    except ModuleNotFoundError:
        # Minimal fallback parser so the feature still works without the dep.
        try:
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = value
            return True
        except OSError:
            return False
    load_dotenv(env_path, override=False)
    return True


DEFAULT_SOURCE_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
    ".rb": "ruby",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "c_sharp",
    ".php": "php",
    ".kt": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
    ".sh": "bash",
    ".bash": "bash",
    ".lua": "lua",
    ".r": "r",
    ".pl": "perl",
    ".dart": "dart",
    ".ex": "elixir",
    ".exs": "elixir",
}

DEFAULT_EXCLUDES: list[str] = [
    ".git/*", ".hg/*", ".svn/*",
    "node_modules/*", "vendor/*", "third_party/*",
    ".venv/*", "venv/*", "env/*", "virtualenv/*",
    "__pycache__/*", "*.egg-info/*", ".eggs/*",
    "dist/*", "build/*", "out/*", "target/*", ".next/*", ".nuxt/*",
    "coverage/*", "htmlcov/*", ".pytest_cache/*", ".mypy_cache/*",
    ".ruff_cache/*", ".tox/*", ".nox/*", ".bugseer/*",
    "*.min.js", "*.bundle.js", "*.min.css", "*.map",
    "*_pb2.py", "*_pb2_grpc.py", "*.pb.go", "*.generated.*", "*.g.dart",
    "migrations/*", "*.lock", "package-lock.json", "yarn.lock", "poetry.lock",
]

TEST_PATH_MARKERS: tuple[str, ...] = (
    "test_", "_test.", "tests/", "test/", "spec/", ".spec.", ".test.",
    "__tests__/", "conftest.py", "testing/",
)


@dataclass
class RuleWeights:
    """Points contributed by each rule. Override any of these in .bugseer.toml.

    The defaults follow the roadmap's table, extended with a few rules that
    consistently correlate with defects in the empirical literature.
    """

    # ---- Phase 1: static analysis -----------------------------------------
    long_function: float = 10.0            # function > threshold lines
    deep_nesting: float = 15.0             # nested loops / blocks > threshold
    high_branching: float = 10.0           # too many if statements
    no_error_handling: float = 20.0        # risky calls with no try/except
    global_variables: float = 15.0
    duplicate_code: float = 10.0
    low_test_coverage: float = 20.0
    high_complexity: float = 12.0          # cyclomatic complexity
    god_file: float = 8.0                  # very large file
    bare_except: float = 8.0               # except: / catch(e){}
    long_parameter_list: float = 5.0
    mutable_default_arg: float = 6.0
    magic_numbers: float = 3.0
    todo_debt: float = 3.0
    low_comment_ratio: float = 3.0

    # ---- Phase 2: git intelligence ----------------------------------------
    change_frequency: float = 15.0         # file changed many times
    bugfix_density: float = 18.0
    revert_history: float = 14.0
    recent_churn: float = 8.0
    many_authors: float = 8.0
    fix_follow_rate: float = 12.0          # edits that needed follow-up fixes
    knowledge_silo: float = 6.0            # single-owner file

    # ---- Phase 5: dependency graph ----------------------------------------
    high_fan_in: float = 10.0              # many files depend on this one
    high_centrality: float = 8.0

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass
class Thresholds:
    """Where each rule starts firing."""

    long_function_lines: int = 100
    very_long_function_lines: int = 200
    max_nesting_depth: int = 3
    nested_loop_depth: int = 3
    branch_count: int = 15
    cyclomatic_complexity: int = 20
    high_cyclomatic_complexity: int = 40
    god_file_loc: int = 600
    huge_file_loc: int = 1200
    max_parameters: int = 6
    duplicate_ratio: float = 0.12
    duplicate_block_lines: int = 6
    low_coverage: float = 0.5
    magic_number_count: int = 12
    todo_count: int = 5
    low_comment_ratio: float = 0.03
    commit_count: int = 20
    high_commit_count: int = 50
    bugfix_ratio: float = 0.25
    revert_count: int = 1
    recent_commits_30d: int = 8
    author_count: int = 5
    fix_follow_rate: float = 0.3
    ownership_ratio: float = 0.9
    fan_in: int = 8

    # Risk bands (0-100)
    band_medium: float = 35.0
    band_high: float = 60.0
    band_critical: float = 85.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Config:
    root: Path = field(default_factory=Path.cwd)
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    extensions: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SOURCE_EXTENSIONS))
    weights: RuleWeights = field(default_factory=RuleWeights)
    thresholds: Thresholds = field(default_factory=Thresholds)
    history_days: int = 730
    max_file_bytes: int = 1_500_000
    workers: int = 0
    coverage_file: str | None = None
    bugfix_pattern: str = ""
    use_git: bool = True
    use_ml: bool = True
    ignore_tests: bool = False
    home: str = ".bugseer"

    # ---- optional AI narrator (never required) ----------------------------
    ai_provider: str = "none"
    ai_send_source: bool = False
    ai_redact_paths: bool = False
    ai_timeout: int = 30
    offline: bool = False

    @property
    def home_path(self) -> Path:
        p = Path(self.home)
        return p if p.is_absolute() else self.root / p

    def is_test_path(self, rel_path: str) -> bool:
        low = rel_path.replace("\\", "/").lower()
        return any(marker in low for marker in TEST_PATH_MARKERS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "include": self.include,
            "exclude": self.exclude,
            "history_days": self.history_days,
            "workers": self.workers,
            "use_git": self.use_git,
            "use_ml": self.use_ml,
            "ignore_tests": self.ignore_tests,
            "weights": self.weights.as_dict(),
            "thresholds": self.thresholds.as_dict(),
            "ai_provider": self.ai_provider,
        }


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def load_config(
    root: str | Path = ".",
    overrides: dict[str, Any] | None = None,
) -> Config:
    """Build the effective configuration for a repository."""
    root_path = Path(root).resolve()
    load_dotenv_if_present(root_path)

    cfg = Config(root=root_path)

    # ---- environment -------------------------------------------------------
    cfg.home = os.environ.get("BUGSEER_HOME", cfg.home)
    cfg.history_days = _env_int("BUGSEER_HISTORY_DAYS", cfg.history_days)
    cfg.workers = _env_int("BUGSEER_WORKERS", cfg.workers)
    cfg.coverage_file = os.environ.get("BUGSEER_COVERAGE_FILE") or None
    cfg.bugfix_pattern = os.environ.get("BUGSEER_BUGFIX_PATTERN", "")
    cfg.ai_provider = (os.environ.get("BUGSEER_AI_PROVIDER") or "none").strip().lower()
    cfg.ai_send_source = _env_bool("BUGSEER_AI_SEND_SOURCE", False)
    cfg.ai_redact_paths = _env_bool("BUGSEER_AI_REDACT_PATHS", False)
    cfg.ai_timeout = _env_int("BUGSEER_AI_TIMEOUT", 30)
    cfg.offline = _env_bool("BUGSEER_OFFLINE", False)

    # ---- .bugseer.toml -----------------------------------------------------
    toml_path = root_path / ".bugseer.toml"
    if toml_path.is_file():
        try:
            data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a malformed config must not crash a scan
            data = {}
        section = data.get("bugseer", data)

        for key in (
            "history_days", "max_file_bytes", "workers", "coverage_file",
            "bugfix_pattern", "use_git", "use_ml", "ignore_tests", "home",
        ):
            if key in section:
                setattr(cfg, key, section[key])
        if "include" in section:
            cfg.include = list(section["include"])
        if "exclude" in section:
            # User excludes extend the defaults rather than replacing them.
            cfg.exclude = list(DEFAULT_EXCLUDES) + list(section["exclude"])
        if "extensions" in section and isinstance(section["extensions"], dict):
            cfg.extensions.update(section["extensions"])
        for key, value in (section.get("weights") or {}).items():
            if hasattr(cfg.weights, key):
                setattr(cfg.weights, key, float(value))
        for key, value in (section.get("thresholds") or {}).items():
            if hasattr(cfg.thresholds, key):
                current = getattr(cfg.thresholds, key)
                setattr(cfg.thresholds, key, type(current)(value))

    # ---- explicit overrides (CLI) -----------------------------------------
    for key, value in (overrides or {}).items():
        if value is None:
            continue
        if key == "exclude" and value:
            cfg.exclude = list(cfg.exclude) + list(value)
        elif key == "include" and value:
            cfg.include = list(value)
        elif hasattr(cfg, key):
            setattr(cfg, key, value)

    return cfg
