from setuptools import setup, find_packages

long_description = """# 🔍 BugSeer

**Offline bug-risk prediction for code repositories.**
Static analysis + git intelligence + a local ML model — and an explanation for every number it shows you.

No cloud. No API keys. No telemetry. Your source code never leaves your machine.

---

## Why this exists

Most "AI code review" tools give you a number and expect you to trust it. Developers don't, and they're right not to.

BugSeer's rule is simple: **every point of risk traces back to named, inspectable evidence.**

```
🔥 src/payment.py   96/100 (critical)
████████████████████████████████████████
static 102  ·  git 70  ·  raw 172 pts

  ⎇ +40 High bug-fix density  [bugfix-density]
      9 of 10 commits (90%) look like bug fixes. Code that has needed
      repeated repair tends to need more.

  ◆ +27 No error handling around risky operations  [no-error-handling]
      4 I/O, network, parsing or subprocess call(s) and zero try/catch
      blocks. Failures here surface as unhandled exceptions in production.

  ◆ +25 Deeply nested control flow  [deep-nesting]
      Maximum loop nesting is 3 and maximum block depth is 7 (threshold 3).
      ↳ L12 (depth 7), L28 (depth 7), L11 (depth 6)

  ⎇ +14 Previously reverted  [revert-history]
      1 revert/rollback commit(s) touched this file. A revert is direct
      evidence that a change here broke something in production.
```

---

## Key Features

- **Rule-based static analysis**: AST analysis for Python, tree-sitter support for 19+ languages, and heuristic parsing.
- **Git intelligence**: Churn analysis, bug-fix commit density, co-changing file couplings, and revert history.
- **Local Machine Learning**: Scikit-learn, XGBoost, or LightGBM models trained on commit history—no cloud training.
- **Dependency & Impact graph**: Predict ripple effect and blast radius of changing any file.
- **Self-contained HTML reports & Dashboard**: Interactive web UI powered by FastAPI.
- **100% Offline & Private**: Zero network dependencies, zero cloud API calls, zero telemetry.

---

## Install

```bash
pip install bugseer            # core installation
pip install bugseer[all]       # full installation with multi-language parsers & ML
```

## Quick Start

```bash
bugseer scan .                        # rank files by risk
bugseer explain src/payment.py        # full reasoning for one file
bugseer heatmap .                     # colour-coded project tree
bugseer impact src/database.py        # "what if I change this?"
bugseer train .                       # learn from your own bug history
bugseer serve .                       # interactive dashboard
bugseer report . -o risk.html         # shareable offline HTML
```
"""

setup(
    name="bugseer",
    version="0.1.0",
    description="Offline bug-risk prediction for code repositories. Static analysis + git intelligence + local ML. No cloud, no API keys required.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Rishi Banota",
    url="https://github.com/rishibanota/bugseer",
    project_urls={
        "Homepage": "https://github.com/rishibanota/bugseer",
        "Source Code": "https://github.com/rishibanota/bugseer",
        "Issue Tracker": "https://github.com/rishibanota/bugseer/issues",
    },
    packages=find_packages(),
    include_package_data=True,
    package_data={
        "bugseer": [
            "webui/static/index.html",
            "webui/static/assets/*",
        ],
    },
    python_requires=">=3.10",
    install_requires=[
        "typer>=0.12",
        "rich>=13.0",
        "pydantic>=2.0",
        "networkx>=3.0",
        "jinja2>=3.1",
        "numpy>=1.24",
        "pandas>=2.0",
        "scikit-learn>=1.3",
        "joblib>=1.3",
    ],
    extras_require={
        "parsers": ["tree-sitter>=0.21", "tree-sitter-language-pack>=0.2"],
        "server": ["fastapi>=0.110", "uvicorn[standard]>=0.27"],
        "ml": ["xgboost>=2.0", "lightgbm>=4.0"],
        "env": ["python-dotenv>=1.0"],
        "dev": ["pytest>=7.4", "pytest-cov>=4.1"],
        "all": [
            "tree-sitter>=0.21",
            "tree-sitter-language-pack>=0.2",
            "fastapi>=0.110",
            "uvicorn[standard]>=0.27",
            "xgboost>=2.0",
            "lightgbm>=4.0",
            "python-dotenv>=1.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "bugseer = bugseer.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Quality Assurance",
        "Topic :: Software Development :: Testing",
    ],
    keywords=["static-analysis", "bug-prediction", "git", "code-quality", "machine-learning", "offline"],
)
