"""Shared fixtures: builds a small throwaway git repository to analyse."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

RISKY = '''
import requests, json
CACHE = {}

def charge(order, user, gateway, retries, currency, opts=[]):
    global CACHE
    total = 0
    for item in order.items:
        for tax in item.taxes:
            for rule in tax.rules:
                if rule.active:
                    if rule.kind == 7:
                        total += rule.rate * 1.375
    response = requests.post("http://gw", json={"t": total})
    return json.loads(response.text)
'''

SAFE = '''
def slugify(text):
    """Lowercase and hyphenate."""
    return text.lower().replace(" ", "-")
'''

DB = '''
import sqlite3

def query(dsn, sql):
    try:
        conn = sqlite3.connect(dsn)
        return conn.execute(sql).fetchall()
    except sqlite3.Error:
        return []
'''

API = '''
from src import payment
from src import database

def handler(request):
    if request.kind == "pay":
        return payment.charge(request.order, request.user, None, 3, "usd")
    return database.query("db", "select 1")
'''


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@e.com",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@e.com",
        },
    )


@pytest.fixture(scope="session")
def demo_repo(tmp_path_factory) -> Path:
    """A git repo with a deliberately risky file and a deliberately clean one."""
    root = tmp_path_factory.mktemp("demo_repo")
    (root / "src").mkdir()
    (root / "tests").mkdir()

    (root / "src" / "payment.py").write_text(RISKY, encoding="utf-8")
    (root / "src" / "utils.py").write_text(SAFE, encoding="utf-8")
    (root / "src" / "database.py").write_text(DB, encoding="utf-8")
    (root / "src" / "api.py").write_text(API, encoding="utf-8")
    (root / "tests" / "test_utils.py").write_text(
        "from src.utils import slugify\n\ndef test_slugify():\n"
        "    assert slugify('A B') == 'a-b'\n",
        encoding="utf-8",
    )

    _git(["init", "-q"], root)
    _git(["config", "user.email", "t@e.com"], root)
    _git(["config", "user.name", "Test"], root)
    _git(["add", "-A"], root)
    _git(["commit", "-qm", "feat: initial import"], root)

    # payment.py accumulates bug fixes; utils.py stays calm.
    payment = root / "src" / "payment.py"
    for i in range(8):
        payment.write_text(payment.read_text(encoding="utf-8") + f"\n# tweak {i}\n",
                           encoding="utf-8")
        _git(["add", "-A"], root)
        _git(["commit", "-qm", f"fix: correct rounding error {i}"], root)

    _git(["commit", "--allow-empty", "-qm", 'Revert "fix: correct rounding error 7"'], root)

    utils = root / "src" / "utils.py"
    utils.write_text(utils.read_text(encoding="utf-8") + "\n# docs\n", encoding="utf-8")
    _git(["add", "-A"], root)
    _git(["commit", "-qm", "docs: clarify slugify"], root)

    return root


@pytest.fixture(scope="session")
def plain_dir(tmp_path_factory) -> Path:
    """A directory that is deliberately NOT a git repository."""
    root = tmp_path_factory.mktemp("plain_dir")
    (root / "mod.py").write_text(SAFE, encoding="utf-8")
    return root
