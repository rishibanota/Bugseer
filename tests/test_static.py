"""Phase 1: static analysis correctness."""

from __future__ import annotations

import pytest

from bugseer.analysis.static import analyze_source, tree_sitter_available
from bugseer.analysis.duplication import internal_duplication, normalize_line

PY_RISKY = '''
import os, requests, json
CACHE = {}

def process(order, user, gateway, retries, currency, opts=[]):
    global CACHE
    total = 0
    for item in order.items:
        for tax in item.taxes:
            for rule in tax.rules:
                if rule.active:
                    if rule.kind == 7:
                        total += rule.rate * 1.375
    response = requests.post("http://gw", json={"t": total})
    data = json.loads(response.text)
    handle = open("/tmp/x", "w")
    return total
'''


class TestPythonAnalyzer:
    def test_uses_ast_backend(self):
        m = analyze_source(PY_RISKY, "p.py", "python")
        assert m.parser == "ast"
        assert m.parse_ok

    def test_counts_functions_and_nesting(self):
        m = analyze_source(PY_RISKY, "p.py", "python")
        assert m.function_count == 1
        assert m.nested_loop_depth == 3
        assert m.max_nesting_depth >= 5

    def test_detects_globals_and_mutable_defaults(self):
        m = analyze_source(PY_RISKY, "p.py", "python")
        assert m.global_variables >= 1
        assert "CACHE" in m.global_names
        assert m.mutable_default_args == 1

    def test_detects_risky_calls_without_handling(self):
        m = analyze_source(PY_RISKY, "p.py", "python")
        assert m.risky_calls >= 3
        assert m.try_blocks == 0

    def test_detects_bare_except_and_swallowing(self):
        source = "def f():\n    try:\n        g()\n    except:\n        pass\n"
        m = analyze_source(source, "p.py", "python")
        assert m.bare_excepts == 1
        assert m.swallowed_exceptions == 1
        assert m.try_blocks == 1

    def test_collects_imports(self):
        m = analyze_source(PY_RISKY, "p.py", "python")
        assert "requests" in m.imports
        assert "json" in m.imports

    def test_complexity_increases_with_branches(self):
        simple = analyze_source("def f():\n    return 1\n", "a.py", "python")
        complex_ = analyze_source(
            "def f(x):\n" + "".join(f"    if x == {i}: return {i}\n" for i in range(12)),
            "b.py", "python",
        )
        assert complex_.cyclomatic_complexity > simple.cyclomatic_complexity
        assert complex_.max_function_complexity >= 12

    def test_syntax_error_falls_back_gracefully(self):
        m = analyze_source("def broken(:\n  pass\n", "bad.py", "python")
        assert m.parse_ok is False
        assert "SyntaxError" in m.parse_error
        # Still produced usable line metrics via the heuristic backend.
        assert m.total_lines == 2

    def test_long_function_recorded_with_location(self):
        body = "\n".join(f"    x{i} = {i}" for i in range(120))
        m = analyze_source(f"def big():\n{body}\n", "p.py", "python")
        assert m.max_function_length > 100
        assert m.long_functions[0]["name"] == "big"
        assert m.long_functions[0]["line"] == 1

    def test_empty_file_is_safe(self):
        m = analyze_source("", "empty.py", "python")
        assert m.loc == 0
        assert m.parse_ok


JS_SOURCE = """
function handle(order, user, cfg, retries, logger, extra) {
  var CACHE = {};
  for (const i of order.items) {
    for (const t of i.taxes) {
      for (const r of t.rules) {
        if (r.active && r.kind === 7) { total += r.rate * 1.375; }
      }
    }
  }
  try { fetch("http://x"); } catch (e) {}
  return total;
}
"""


@pytest.mark.skipif(not tree_sitter_available(), reason="tree-sitter not installed")
class TestTreeSitterBackend:
    def test_javascript_uses_tree_sitter(self):
        m = analyze_source(JS_SOURCE, "a.js", "javascript")
        assert m.parser == "tree-sitter"

    def test_javascript_nesting_and_params(self):
        m = analyze_source(JS_SOURCE, "a.js", "javascript")
        assert m.nested_loop_depth == 3
        assert m.max_parameters == 6
        assert m.except_handlers == 1
        assert m.swallowed_exceptions == 1

    @pytest.mark.parametrize(
        "language,source",
        [
            ("go", "package m\nfunc F() { for i:=0;i<3;i++ { if i>1 { return } } }\n"),
            ("java", "class A { void f() { for(int i=0;i<3;i++){ if(i>1){return;} } } }"),
            ("rust", "fn f() { for i in 0..3 { if i > 1 { return; } } }"),
            ("ruby", "def f\n  [1].each do |i|\n    if i > 1\n      return\n    end\n  end\nend"),
        ],
    )
    def test_multi_language_smoke(self, language, source):
        m = analyze_source(source, f"a.{language}", language)
        assert m.parse_ok or m.parse_error
        assert m.function_count >= 1
        assert m.cyclomatic_complexity >= 1


class TestHeuristicFallback:
    def test_unknown_language_still_analyzed(self):
        source = "function f() {\n  if (x) {\n    for (i) {\n      y();\n    }\n  }\n}\n"
        m = analyze_source(source, "a.zzz", "unknown-lang")
        assert m.parser == "heuristic"
        assert m.function_count >= 1
        assert m.loc > 0


class TestDuplication:
    def test_normalize_collapses_literals(self):
        assert normalize_line('  x = "hello" + 42  ') == normalize_line("x = 'world' + 7")

    def test_comments_are_ignored(self):
        assert normalize_line("# a comment") == ""
        assert normalize_line("// a comment") == ""

    def test_detects_repeated_blocks(self):
        block = [
            "alpha = compute(1)", "beta = compute(2)", "gamma = compute(3)",
            "delta = combine(alpha, beta)", "epsilon = refine(delta)",
            "zeta = finalize(epsilon)",
        ]
        lines = block + ["", "separator()", ""] + block
        count, ratio = internal_duplication(lines)
        assert count >= 1
        assert ratio > 0

    def test_unique_code_has_no_duplication(self):
        lines = [f"unique_{i} = compute_{i}(value_{i})" for i in range(20)]
        count, ratio = internal_duplication(lines)
        assert count == 0
        assert ratio == 0.0
