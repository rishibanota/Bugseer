# Contributing to BugSeer

Thank you for your interest in contributing to **BugSeer**! We welcome bug reports, feature suggestions, documentation improvements, parser additions, and pull requests.

BugSeer is designed as a privacy-focused, offline-first tool for bug-risk prediction that runs without cloud dependencies or external API keys.

---

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [How Can I Contribute?](#how-can-i-contribute)
  - [Reporting Bugs](#reporting-bugs)
  - [Suggesting Features & Rules](#suggesting-features--rules)
  - [Adding Language Parsers or Static Rules](#adding-language-parsers-or-static-rules)
  - [Submitting Pull Requests](#submitting-pull-requests)
- [Development Setup](#development-setup)
- [Running Tests](#running-tests)
- [Project Architecture](#project-architecture)
- [Style & Quality Guidelines](#style--quality-guidelines)

---

## Code of Conduct

This project and everyone participating in it is governed by the [BugSeer Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

---

## How Can I Contribute?

### Reporting Bugs

Before creating a bug report, please check existing issues to see if the problem has already been reported. When creating an issue, please use our [Bug Report Template](.github/ISSUE_TEMPLATE/bug_report.md) and include:

- A clear and descriptive title.
- Steps to reproduce the behavior.
- Operating system and Python version.
- Exact CLI command run (`bugseer scan ...`, `bugseer explain ...`, etc.).
- Expected vs. actual output (including full error tracebacks if applicable).

### Suggesting Features & Rules

We love ideas for new risk heuristic rules, local ML algorithms, and UI enhancements! Please submit feature requests using our [Feature Request Template](.github/ISSUE_TEMPLATE/feature_request.md). Describe:

- The problem or missing capability.
- The proposed solution or rule logic.
- Rationale for why this helps developers identify software defect risks.

### Adding Language Parsers or Static Rules

BugSeer supports both exact stdlib AST parsing (Python), multi-language parsing via tree-sitter, and heuristic fallbacks:

- Static rules live in [`bugseer/rules.py`](file:///C:/Users/Rishi/Documents/GitHub/Bugseer/bugseer/rules.py).
- Language analyzer modules live in [`bugseer/analysis/`](file:///C:/Users/Rishi/Documents/GitHub/Bugseer/bugseer/analysis/).

If you are adding a new static analysis rule or extending multi-language support, ensure your rule:
1. Traces back to named, inspectable evidence.
2. Returns clear explanation strings for the report output.
3. Includes unit test coverage in `tests/`.

---

## Development Setup

1. **Fork and Clone the Repository**
   ```bash
   git clone https://github.com/YOUR-USERNAME/bugseer.git
   cd bugseer
   ```

2. **Set Up a Virtual Environment**
   ```bash
   python -m venv venv
   # On Linux/macOS:
   source venv/bin/activate
   # On Windows (PowerShell):
   .\venv\Scripts\Activate.ps1
   ```

3. **Install Editable Package with Development Dependencies**
   ```bash
   pip install -e ".[dev,all]"
   ```

---

## Running Tests

Verify that your local setup is working and all tests pass before making changes:

```bash
# Run all tests
pytest

# Run tests with coverage report
pytest --cov=bugseer
```

Please add new unit tests for any bug fixes or new features you introduce under `tests/`.

---

## Project Architecture

- **`bugseer/cli.py`**: Typer-based CLI command runner (`scan`, `explain`, `heatmap`, `impact`, `train`, `serve`, `report`).
- **`bugseer/rules.py` & `bugseer/analysis/`**: Static analysis engine and language-specific AST/tree-sitter/heuristic parsers.
- **`bugseer/git_intel.py`**: Git commit history analyzer (churn, fix-density, co-change, revert detection).
- **`bugseer/ml.py`**: Local machine learning scoring model (Gradient Boosting / scikit-learn / XGBoost / LightGBM).
- **`bugseer/graph.py`**: Dependency graph & ripple impact analysis engine.
- **`bugseer/report.py` & `bugseer/server.py`**: Self-contained HTML report renderer & FastAPI dashboard server.

---

## Style & Quality Guidelines

- **Python Version**: Python 3.10+ compatible syntax.
- **Type Annotations**: Use type hints wherever practical.
- **Privacy First**: BugSeer must remain 100% offline. Never introduce external API calls or network telemetry.
- **Clean Commits**: Write clear, imperative commit messages (e.g., `Add static rule for SQL injection detection`).

Thank you for helping make codebases safer and more inspectable with BugSeer!

