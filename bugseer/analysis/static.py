"""Phase 1 static analysis.

Three backends, tried in order of fidelity:
  1. Python  -> stdlib `ast` (exact, zero dependencies)
  2. Others  -> tree-sitter, if `tree-sitter-language-pack` is installed
  3. Fallback-> a brace/indent heuristic that never fails

The output is always a `FileMetrics`, so downstream phases never care which
backend produced it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from bugseer.analysis.duplication import fingerprints, internal_duplication
from bugseer.analysis.langspec import get_spec
from bugseer.models import FileMetrics

# --------------------------------------------------------------------------
# Optional tree-sitter backend
# --------------------------------------------------------------------------
_TS_AVAILABLE: bool | None = None
_TS_PARSERS: dict[str, Any] = {}


def tree_sitter_available() -> bool:
    global _TS_AVAILABLE
    if _TS_AVAILABLE is None:
        try:
            import tree_sitter_language_pack  # noqa: F401
            _TS_AVAILABLE = True
        except Exception:  # noqa: BLE001
            _TS_AVAILABLE = False
    return _TS_AVAILABLE


def _get_ts_parser(language: str):
    if language in _TS_PARSERS:
        return _TS_PARSERS[language]
    if not tree_sitter_available():
        _TS_PARSERS[language] = None
        return None
    try:
        from tree_sitter_language_pack import get_parser
        parser = get_parser(language)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 - unavailable grammar, fall back silently
        parser = None
    _TS_PARSERS[language] = parser
    return parser


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------
RISKY_CALL_PATTERN = re.compile(
    r"\b("
    r"open|read|write|requests?|urlopen|fetch|axios|http|socket|connect|"
    r"execute|executemany|query|cursor|commit|rollback|session|transaction|"
    r"subprocess|popen|system|exec|eval|spawn|fork|"
    r"json\.loads?|loads|parse|decode|encode|"
    r"int|float|atoi|parseInt|parseFloat|"
    r"remove|unlink|rmtree|delete|drop|truncate|"
    r"send|recv|publish|consume|acquire|lock|"
    r"os\.environ|getenv|config"
    r")\b",
    re.IGNORECASE,
)

_MAGIC_NUMBER_ALLOW = {"0", "1", "2", "-1", "100", "1000", "0.0", "1.0", "10", "24", "60", "1024"}

_COMMENT_LINE = re.compile(r"^\s*(#|//|/\*|\*|--|<!--|%)")
_TODO = re.compile(r"\b(TODO|FIXME|HACK|XXX|BUG|WORKAROUND|TEMP|KLUDGE|REFACTOR)\b")


def _count_line_kinds(lines: list[str]) -> tuple[int, int, int]:
    """Return (code, blank, comment) line counts."""
    blank = comment = code = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            blank += 1
        elif _COMMENT_LINE.match(line):
            comment += 1
        else:
            code += 1
    return code, blank, comment


def _finalize(metrics: FileMetrics, lines: list[str], source: str) -> FileMetrics:
    """Fill in language-agnostic metrics shared by every backend."""
    code, blank, comment = _count_line_kinds(lines)
    metrics.total_lines = len(lines)
    metrics.loc = code
    metrics.blank_lines = blank
    metrics.comment_lines = comment
    metrics.comment_ratio = comment / max(1, code + comment)
    metrics.todo_comments = len(_TODO.findall(source))

    dup_blocks, dup_ratio = internal_duplication(lines)
    metrics.duplicate_block_count = dup_blocks
    metrics.duplicate_line_ratio = dup_ratio

    if metrics.function_count and metrics.avg_function_length == 0.0:
        metrics.avg_function_length = metrics.loc / metrics.function_count

    metrics.long_functions.sort(key=lambda f: f.get("length", 0), reverse=True)
    metrics.long_functions = metrics.long_functions[:12]
    metrics.deep_nesting_sites.sort(key=lambda f: f.get("depth", 0), reverse=True)
    metrics.deep_nesting_sites = metrics.deep_nesting_sites[:12]
    return metrics


# ==========================================================================
# Backend 1: Python stdlib `ast`
# ==========================================================================
class _PyVisitor(ast.NodeVisitor):
    BRANCH_NODES = (ast.If, ast.IfExp, ast.Match)
    LOOP_NODES = (ast.For, ast.AsyncFor, ast.While, ast.comprehension)

    def __init__(self, source_lines: list[str]) -> None:
        self.lines = source_lines
        self.m = FileMetrics(path="", language="python", parser="ast")
        self.depth = 0
        self.loop_depth = 0
        self._func_stack: list[dict[str, Any]] = []
        self._module_assign_names: set[str] = set()
        self._class_depth = 0

    # -- helpers ---------------------------------------------------------
    def _enter_block(self) -> None:
        self.depth += 1
        self.m.max_nesting_depth = max(self.m.max_nesting_depth, self.depth)
        if self.depth >= 4:
            node_line = getattr(self, "_current_line", 0)
            self.m.deep_nesting_sites.append({"line": node_line, "depth": self.depth})

    def _exit_block(self) -> None:
        self.depth -= 1

    def _bump_complexity(self, amount: int = 1) -> None:
        self.m.cyclomatic_complexity += amount
        if self._func_stack:
            self._func_stack[-1]["complexity"] += amount
        # Cognitive complexity weights deeply nested branches more heavily.
        self.m.cognitive_complexity += amount * max(1, self.depth)

    # -- definitions -----------------------------------------------------
    def _visit_function(self, node: ast.AST) -> None:
        name = getattr(node, "name", "<lambda>")
        start = getattr(node, "lineno", 0)
        end = getattr(node, "end_lineno", start) or start
        length = max(1, end - start + 1)

        self.m.function_count += 1
        self.m.max_function_length = max(self.m.max_function_length, length)

        args = getattr(node, "args", None)
        n_params = 0
        if args is not None:
            n_params = (
                len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
                + (1 if args.vararg else 0) + (1 if args.kwarg else 0)
            )
            self.m.max_parameters = max(self.m.max_parameters, n_params)
            for default in list(args.defaults) + [d for d in args.kw_defaults if d]:
                if isinstance(default, (ast.List, ast.Dict, ast.Set, ast.Call)):
                    self.m.mutable_default_args += 1

        self._func_stack.append({"name": name, "complexity": 1, "line": start})
        self.m.long_functions.append(
            {"name": name, "line": start, "end_line": end, "length": length,
             "params": n_params, "complexity": 0}
        )
        record = self.m.long_functions[-1]

        self._enter_block()
        self.generic_visit(node)
        self._exit_block()

        frame = self._func_stack.pop()
        record["complexity"] = frame["complexity"]
        self.m.max_function_complexity = max(self.m.max_function_complexity, frame["complexity"])

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        self.m.function_count += 1
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.m.class_count += 1
        self._class_depth += 1
        self._enter_block()
        self.generic_visit(node)
        self._exit_block()
        self._class_depth -= 1

    # -- control flow ----------------------------------------------------
    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        self._current_line = node.lineno
        self.m.branch_count += 1
        self._bump_complexity()
        self._enter_block()
        self.generic_visit(node)
        self._exit_block()

    def visit_IfExp(self, node: ast.IfExp) -> None:  # noqa: N802
        self.m.branch_count += 1
        self._bump_complexity()
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:  # noqa: N802
        self.m.branch_count += len(node.cases)
        self._bump_complexity(len(node.cases))
        self._enter_block()
        self.generic_visit(node)
        self._exit_block()

    def _visit_loop(self, node: ast.AST) -> None:
        self._current_line = getattr(node, "lineno", 0)
        self.m.loop_count += 1
        self._bump_complexity()
        self.loop_depth += 1
        self.m.nested_loop_depth = max(self.m.nested_loop_depth, self.loop_depth)
        self._enter_block()
        self.generic_visit(node)
        self._exit_block()
        self.loop_depth -= 1

    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:  # noqa: N802
        self._visit_loop(node)

    def visit_While(self, node: ast.While) -> None:  # noqa: N802
        self._visit_loop(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:  # noqa: N802
        self._bump_complexity(max(0, len(node.values) - 1))
        self.generic_visit(node)

    # -- error handling --------------------------------------------------
    def visit_Try(self, node: ast.Try) -> None:  # noqa: N802
        self.m.try_blocks += 1
        self.m.except_handlers += len(node.handlers)
        for handler in node.handlers:
            self._bump_complexity()
            if handler.type is None:
                self.m.bare_excepts += 1
            body = handler.body
            only_pass = len(body) == 1 and isinstance(body[0], (ast.Pass, ast.Ellipsis))
            if only_pass:
                self.m.swallowed_exceptions += 1
            elif len(body) == 1 and isinstance(body[0], ast.Expr):
                value = body[0].value
                if isinstance(value, ast.Constant) and value.value is Ellipsis:
                    self.m.swallowed_exceptions += 1
        self._enter_block()
        self.generic_visit(node)
        self._exit_block()

    def visit_With(self, node: ast.With) -> None:  # noqa: N802
        self._enter_block()
        self.generic_visit(node)
        self._exit_block()

    # -- data flow -------------------------------------------------------
    def visit_Global(self, node: ast.Global) -> None:  # noqa: N802
        self.m.global_variables += len(node.names)
        self.m.global_names.extend(node.names)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        # Module-level mutable state is a classic source of coupling bugs.
        if not self._func_stack and self._class_depth == 0:
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.isupper():
                    if isinstance(node.value, (ast.List, ast.Dict, ast.Set, ast.Call)):
                        self._module_assign_names.add(target.id)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        try:
            src = ast.unparse(node.func)
        except Exception:  # noqa: BLE001
            src = ""
        if src and RISKY_CALL_PATTERN.search(src):
            self.m.risky_calls += 1
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            if str(node.value) not in _MAGIC_NUMBER_ALLOW:
                self.m.magic_numbers += 1
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            self.m.imports.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        module = node.module or ""
        prefix = "." * (node.level or 0)
        self.m.imports.append(f"{prefix}{module}")
        for alias in node.names:
            self.m.imports.append(f"{prefix}{module}.{alias.name}" if module else f"{prefix}{alias.name}")
        self.generic_visit(node)


def _analyze_python(source: str, path: str) -> FileMetrics:
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        m = _analyze_heuristic(source, path, "python")
        m.parse_ok = False
        m.parse_error = f"SyntaxError: {exc.msg} (line {exc.lineno})"
        return m

    visitor = _PyVisitor(lines)
    visitor.visit(tree)
    m = visitor.m
    m.path = path
    m.global_variables += len(visitor._module_assign_names)
    m.global_names.extend(sorted(visitor._module_assign_names))
    m.global_names = sorted(set(m.global_names))[:20]
    if m.function_count:
        total = sum(f["length"] for f in m.long_functions)
        m.avg_function_length = total / max(1, len(m.long_functions))
    return _finalize(m, lines, source)


# ==========================================================================
# Backend 2: tree-sitter
# ==========================================================================
def _analyze_tree_sitter(source: str, path: str, language: str) -> FileMetrics | None:
    spec = get_spec(language)
    parser = _get_ts_parser(language)
    if spec is None or parser is None:
        return None

    data = source.encode("utf-8", errors="replace")
    try:
        tree = parser.parse(data)
    except Exception:  # noqa: BLE001
        return None

    m = FileMetrics(path=path, language=language, parser="tree-sitter")
    lines = source.splitlines()

    def node_text(node) -> str:
        try:
            return data[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return ""

    def func_name(node) -> str:
        for child in node.children:
            if child.type in ("identifier", "field_identifier", "property_identifier",
                              "name", "simple_identifier", "type_identifier"):
                return node_text(child)
        named = node.child_by_field_name("name")
        if named is not None:
            return node_text(named)
        return "<anonymous>"

    def count_params(node) -> int:
        for child in node.children:
            if child.type in spec.param_nodes:
                return sum(
                    1 for c in child.children
                    if c.is_named and c.type not in ("comment",)
                )
        return 0

    stack_state = {"depth": 0, "loop_depth": 0, "func_complexity": []}

    def walk(node, depth: int) -> None:
        ntype = node.type
        is_block = ntype in spec.block_nodes
        is_loop = ntype in spec.loop_nodes
        is_func = ntype in spec.function_nodes

        if is_func:
            start = node.start_point[0] + 1
            end = node.end_point[0] + 1
            length = max(1, end - start + 1)
            m.function_count += 1
            m.max_function_length = max(m.max_function_length, length)
            n_params = count_params(node)
            m.max_parameters = max(m.max_parameters, n_params)
            m.long_functions.append({
                "name": func_name(node), "line": start, "end_line": end,
                "length": length, "params": n_params, "complexity": 0,
            })
            stack_state["func_complexity"].append([len(m.long_functions) - 1, 1])

        if ntype in spec.class_nodes:
            m.class_count += 1

        if ntype in spec.branch_nodes:
            m.branch_count += 1
            m.cyclomatic_complexity += 1
            m.cognitive_complexity += max(1, stack_state["depth"])
            if stack_state["func_complexity"]:
                stack_state["func_complexity"][-1][1] += 1

        if is_loop:
            m.loop_count += 1
            m.cyclomatic_complexity += 1
            m.cognitive_complexity += max(1, stack_state["depth"])
            if stack_state["func_complexity"]:
                stack_state["func_complexity"][-1][1] += 1
            stack_state["loop_depth"] += 1
            m.nested_loop_depth = max(m.nested_loop_depth, stack_state["loop_depth"])

        if ntype in spec.try_nodes:
            m.try_blocks += 1
        if ntype in spec.catch_nodes:
            m.except_handlers += 1
            m.cyclomatic_complexity += 1
            body_text = node_text(node)
            inner = body_text[body_text.find("{") + 1 : body_text.rfind("}")] if "{" in body_text else ""
            if inner.strip() in ("", ";") or re.fullmatch(r"\s*(pass|;|//.*|#.*)?\s*", inner or ""):
                m.swallowed_exceptions += 1
            # `catch (e)` / bare `except` with no type filter
            if re.search(r"catch\s*\(\s*(\w+\s*)?\)|except\s*:", body_text[:80]):
                m.bare_excepts += 1

        if ntype in spec.boolean_op_nodes:
            text = node_text(node)
            if "&&" in text or "||" in text or re.search(r"\b(and|or)\b", text):
                m.cyclomatic_complexity += 1
                if stack_state["func_complexity"]:
                    stack_state["func_complexity"][-1][1] += 1

        if ntype in spec.ternary_nodes:
            m.cyclomatic_complexity += 1

        if ntype in spec.call_nodes:
            text = node_text(node)[:160]
            if RISKY_CALL_PATTERN.search(text):
                m.risky_calls += 1

        if ntype in spec.import_nodes:
            text = node_text(node)
            for match in re.findall(r"""["'<]([^"'>\n]+)["'>]""", text):
                m.imports.append(match)
            for match in re.findall(r"\b(?:import|from|use|require|include)\s+([\w./:\\-]+)", text):
                m.imports.append(match.strip())

        if ntype in spec.global_markers:
            m.global_variables += 1
            name = func_name(node)
            if name and name != "<anonymous>":
                m.global_names.append(name)

        if ntype in spec.number_nodes:
            text = node_text(node)
            if text not in _MAGIC_NUMBER_ALLOW:
                m.magic_numbers += 1

        # Track nesting depth on structural blocks only.
        entered = False
        if is_block or is_func:
            stack_state["depth"] += 1
            entered = True
            m.max_nesting_depth = max(m.max_nesting_depth, stack_state["depth"])
            if stack_state["depth"] >= 4:
                m.deep_nesting_sites.append(
                    {"line": node.start_point[0] + 1, "depth": stack_state["depth"]}
                )

        for child in node.children:
            walk(child, depth + 1)

        if entered:
            stack_state["depth"] -= 1
        if is_loop:
            stack_state["loop_depth"] -= 1
        if is_func and stack_state["func_complexity"]:
            idx, complexity = stack_state["func_complexity"].pop()
            m.long_functions[idx]["complexity"] = complexity
            m.max_function_complexity = max(m.max_function_complexity, complexity)

    walk(tree.root_node, 0)

    # Module-level `var`/`let`/global-ish declarations for C-family languages.
    if language in ("javascript", "typescript", "tsx", "jsx"):
        m.global_variables += len(re.findall(r"^\s*var\s+\w+", source, re.MULTILINE))
        m.global_variables += len(re.findall(r"^\s*(?:global|window)\.\w+\s*=", source, re.MULTILINE))
    elif language in ("c", "cpp"):
        m.global_variables += len(
            re.findall(r"^(?!\s)(?:static\s+)?[A-Za-z_][\w\s\*]*\s+\w+\s*(?:=|;)", source, re.MULTILINE)
        )
    elif language == "go":
        m.global_variables += len(re.findall(r"^var\s+\w+", source, re.MULTILINE))

    if tree.root_node.has_error:
        m.parse_ok = False
        m.parse_error = "tree-sitter reported syntax errors (partial results)"

    m.imports = sorted(set(i for i in m.imports if i))[:80]
    m.global_names = sorted(set(m.global_names))[:20]
    if m.long_functions:
        m.avg_function_length = sum(f["length"] for f in m.long_functions) / len(m.long_functions)
    return _finalize(m, lines, source)


# ==========================================================================
# Backend 3: heuristic fallback (always works)
# ==========================================================================
_FUNC_PATTERNS = [
    re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\("),                       # python
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*(\w+)?"),  # js
    re.compile(r"^\s*(?:public|private|protected|static|final|\s)*[\w<>\[\],\s]+\s+(\w+)\s*\([^;]*\)\s*\{"),  # java/c#
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)"),                       # go/swift
    re.compile(r"^\s*fn\s+(\w+)"),                                          # rust
    re.compile(r"^\s*(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"),  # js arrow
    re.compile(r"^\s*def\s+(\w+)"),                                         # ruby
    re.compile(r"^\s*sub\s+(\w+)"),                                         # perl
]
_BRANCH_KEYWORDS = re.compile(r"\b(if|elif|elsif|else\s+if|switch|case|when|match|guard|unless)\b")
_LOOP_KEYWORDS = re.compile(r"\b(for|while|foreach|repeat|loop|do)\b")
_TRY_KEYWORDS = re.compile(r"\b(try|begin|rescue|defer)\b")
_CATCH_KEYWORDS = re.compile(r"\b(catch|except|rescue|recover)\b")
_BARE_CATCH = re.compile(r"except\s*:|catch\s*\(\s*\)|catch\s*\{|rescue\s*(?:=>|\n|$)")
_CLASS_KEYWORDS = re.compile(r"^\s*(?:export\s+)?(?:public\s+|private\s+|abstract\s+|final\s+)*"
                             r"(class|struct|interface|trait|module|enum|impl|object)\b")
_GLOBAL_DECL = re.compile(r"^\s*(?:global\s+\w+|var\s+\w+|static\s+[\w\*]+\s+\w+|\$\w+\s*=)")
_IMPORT_LINE = re.compile(
    r"^\s*(?:import|from|#include|require|use|using|package)\s+[\"'<]?([\w./:\\-]+)"
)
_NUMBER_TOKEN = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w.])")


def _indent_width(line: str, tab_size: int = 4) -> int:
    width = 0
    for ch in line:
        if ch == " ":
            width += 1
        elif ch == "\t":
            width += tab_size
        else:
            break
    return width


def _analyze_heuristic(source: str, path: str, language: str) -> FileMetrics:
    lines = source.splitlines()
    m = FileMetrics(path=path, language=language, parser="heuristic")

    brace_depth = 0
    max_brace_depth = 0
    indent_stack: list[int] = []
    loop_depth_stack: list[int] = []
    current_func: dict[str, Any] | None = None
    uses_braces = source.count("{") > max(3, len(lines) * 0.02)

    for idx, raw in enumerate(lines, start=1):
        line = raw.split("//")[0] if "//" in raw else raw
        stripped = line.strip()
        if not stripped or _COMMENT_LINE.match(raw):
            continue

        indent = _indent_width(raw)

        # --- function starts ------------------------------------------
        for pattern in _FUNC_PATTERNS:
            match = pattern.match(line)
            if match:
                if current_func is not None:
                    current_func["end_line"] = idx - 1
                    current_func["length"] = max(1, idx - current_func["line"])
                    m.long_functions.append(current_func)
                    m.max_function_length = max(m.max_function_length, current_func["length"])
                    m.max_function_complexity = max(
                        m.max_function_complexity, current_func["complexity"]
                    )
                params = line.count(",") + 1 if "(" in line and "()" not in line else 0
                current_func = {
                    "name": match.group(1) or "<anonymous>", "line": idx,
                    "end_line": idx, "length": 1, "params": params, "complexity": 1,
                    "indent": indent,
                }
                m.function_count += 1
                m.max_parameters = max(m.max_parameters, params)
                break

        if _CLASS_KEYWORDS.match(line):
            m.class_count += 1

        # --- control flow ---------------------------------------------
        n_branch = len(_BRANCH_KEYWORDS.findall(stripped))
        if n_branch:
            m.branch_count += n_branch
            m.cyclomatic_complexity += n_branch
            if current_func:
                current_func["complexity"] += n_branch

        n_loop = len(_LOOP_KEYWORDS.findall(stripped))
        if n_loop:
            m.loop_count += n_loop
            m.cyclomatic_complexity += n_loop
            if current_func:
                current_func["complexity"] += n_loop

        bool_ops = stripped.count("&&") + stripped.count("||")
        bool_ops += len(re.findall(r"\s(and|or)\s", stripped))
        if bool_ops:
            m.cyclomatic_complexity += bool_ops
            if current_func:
                current_func["complexity"] += bool_ops

        if _TRY_KEYWORDS.search(stripped):
            m.try_blocks += 1
        if _CATCH_KEYWORDS.search(stripped):
            m.except_handlers += 1
            if _BARE_CATCH.search(stripped):
                m.bare_excepts += 1
            nxt = lines[idx].strip() if idx < len(lines) else ""
            if nxt in ("pass", "}", "return", "continue", "") or nxt.startswith("}"):
                m.swallowed_exceptions += 1

        if RISKY_CALL_PATTERN.search(stripped):
            m.risky_calls += 1

        if _GLOBAL_DECL.match(line) and indent == 0:
            m.global_variables += 1

        imp = _IMPORT_LINE.match(line)
        if imp:
            m.imports.append(imp.group(1))

        for token in _NUMBER_TOKEN.findall(stripped):
            if token not in _MAGIC_NUMBER_ALLOW:
                m.magic_numbers += 1

        # --- nesting depth --------------------------------------------
        if uses_braces:
            opens = stripped.count("{")
            closes = stripped.count("}")
            brace_depth += opens - closes
            max_brace_depth = max(max_brace_depth, brace_depth)
            if brace_depth >= 4 and opens:
                m.deep_nesting_sites.append({"line": idx, "depth": brace_depth})
            if n_loop:
                loop_depth_stack.append(brace_depth)
                loop_depth_stack = [d for d in loop_depth_stack if d <= brace_depth]
                m.nested_loop_depth = max(m.nested_loop_depth, len(loop_depth_stack))
        else:
            while indent_stack and indent <= indent_stack[-1]:
                indent_stack.pop()
            if stripped.endswith(":"):
                indent_stack.append(indent)
            depth = len(indent_stack)
            m.max_nesting_depth = max(m.max_nesting_depth, depth)
            if depth >= 4:
                m.deep_nesting_sites.append({"line": idx, "depth": depth})
            if n_loop:
                loop_depth_stack = [d for d in loop_depth_stack if d < indent]
                loop_depth_stack.append(indent)
                m.nested_loop_depth = max(m.nested_loop_depth, len(loop_depth_stack))

    if current_func is not None:
        current_func["end_line"] = len(lines)
        current_func["length"] = max(1, len(lines) - current_func["line"] + 1)
        m.long_functions.append(current_func)
        m.max_function_length = max(m.max_function_length, current_func["length"])
        m.max_function_complexity = max(m.max_function_complexity, current_func["complexity"])

    if uses_braces:
        m.max_nesting_depth = max(m.max_nesting_depth, max_brace_depth)
    m.cognitive_complexity = m.cyclomatic_complexity * max(1, m.max_nesting_depth // 2)
    m.imports = sorted(set(m.imports))[:80]
    if m.long_functions:
        m.avg_function_length = sum(f["length"] for f in m.long_functions) / len(m.long_functions)
    return _finalize(m, lines, source)


# ==========================================================================
# Public entry points
# ==========================================================================
def analyze_source(source: str, path: str, language: str) -> FileMetrics:
    """Analyze in-memory source text and return its metrics."""
    if language == "python":
        metrics = _analyze_python(source, path)
    else:
        metrics = _analyze_tree_sitter(source, path, language)
        if metrics is None:
            metrics = _analyze_heuristic(source, path, language)
    metrics.path = path
    metrics.language = language
    return metrics


def analyze_file(file_path: Path, rel_path: str, language: str,
                 max_bytes: int = 1_500_000) -> tuple[FileMetrics, dict[str, list[int]]]:
    """Analyze a file on disk. Returns (metrics, duplication fingerprints)."""
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        m = FileMetrics(path=rel_path, language=language, parse_ok=False,
                        parse_error=f"unreadable: {exc}")
        return m, {}

    if len(raw) > max_bytes:
        m = FileMetrics(path=rel_path, language=language, parse_ok=False,
                        parse_error=f"skipped: {len(raw)} bytes exceeds limit")
        m.total_lines = raw.count(b"\n")
        m.loc = m.total_lines
        return m, {}

    if b"\x00" in raw[:4096]:
        m = FileMetrics(path=rel_path, language=language, parse_ok=False,
                        parse_error="skipped: binary file")
        return m, {}

    source = raw.decode("utf-8", errors="replace")
    metrics = analyze_source(source, rel_path, language)
    fps = fingerprints(source.splitlines())
    return metrics, fps
