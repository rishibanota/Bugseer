"""Per-language tree-sitter node vocabularies.

Rather than writing a bespoke visitor per language, BugSeer describes each
grammar declaratively: which node types are functions, branches, loops, error
handling, and so on. One generic walker then serves every language, and adding
a new one is a few lines of data.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LangSpec:
    name: str
    function_nodes: frozenset[str] = frozenset()
    class_nodes: frozenset[str] = frozenset()
    branch_nodes: frozenset[str] = frozenset()
    loop_nodes: frozenset[str] = frozenset()
    try_nodes: frozenset[str] = frozenset()
    catch_nodes: frozenset[str] = frozenset()
    throw_nodes: frozenset[str] = frozenset()
    block_nodes: frozenset[str] = frozenset()
    param_nodes: frozenset[str] = frozenset()
    comment_nodes: frozenset[str] = frozenset({"comment"})
    call_nodes: frozenset[str] = frozenset({"call", "call_expression"})
    import_nodes: frozenset[str] = frozenset()
    boolean_op_nodes: frozenset[str] = frozenset()
    ternary_nodes: frozenset[str] = frozenset()
    number_nodes: frozenset[str] = frozenset({"integer", "float", "number"})
    string_nodes: frozenset[str] = frozenset({"string", "string_literal"})
    identifier_nodes: frozenset[str] = frozenset({"identifier"})
    global_markers: frozenset[str] = frozenset()
    extra: dict[str, str] = field(default_factory=dict)


_C_FAMILY_BRANCH = frozenset({"if_statement", "switch_statement", "case_statement",
                              "switch_case", "conditional_expression", "else_clause"})
_C_FAMILY_LOOP = frozenset({"for_statement", "while_statement", "do_statement",
                            "for_in_statement", "for_of_statement", "enhanced_for_statement",
                            "for_range_loop"})

SPECS: dict[str, LangSpec] = {
    "python": LangSpec(
        name="python",
        function_nodes=frozenset({"function_definition", "lambda"}),
        class_nodes=frozenset({"class_definition"}),
        branch_nodes=frozenset({"if_statement", "elif_clause", "match_statement",
                                "case_clause", "conditional_expression"}),
        loop_nodes=frozenset({"for_statement", "while_statement", "list_comprehension",
                              "dictionary_comprehension", "set_comprehension",
                              "generator_expression"}),
        try_nodes=frozenset({"try_statement"}),
        catch_nodes=frozenset({"except_clause", "except_group_clause"}),
        throw_nodes=frozenset({"raise_statement"}),
        block_nodes=frozenset({"block"}),
        param_nodes=frozenset({"parameters", "lambda_parameters"}),
        call_nodes=frozenset({"call"}),
        import_nodes=frozenset({"import_statement", "import_from_statement"}),
        boolean_op_nodes=frozenset({"boolean_operator"}),
        ternary_nodes=frozenset({"conditional_expression"}),
        global_markers=frozenset({"global_statement"}),
    ),
    "javascript": LangSpec(
        name="javascript",
        function_nodes=frozenset({"function_declaration", "function_expression",
                                  "arrow_function", "method_definition",
                                  "generator_function_declaration", "function"}),
        class_nodes=frozenset({"class_declaration", "class"}),
        branch_nodes=_C_FAMILY_BRANCH,
        loop_nodes=_C_FAMILY_LOOP,
        try_nodes=frozenset({"try_statement"}),
        catch_nodes=frozenset({"catch_clause"}),
        throw_nodes=frozenset({"throw_statement"}),
        block_nodes=frozenset({"statement_block", "class_body"}),
        param_nodes=frozenset({"formal_parameters"}),
        call_nodes=frozenset({"call_expression", "new_expression"}),
        import_nodes=frozenset({"import_statement", "import_clause"}),
        boolean_op_nodes=frozenset({"binary_expression"}),
        ternary_nodes=frozenset({"ternary_expression"}),
        number_nodes=frozenset({"number"}),
    ),
    "go": LangSpec(
        name="go",
        function_nodes=frozenset({"function_declaration", "method_declaration", "func_literal"}),
        class_nodes=frozenset({"type_declaration"}),
        branch_nodes=frozenset({"if_statement", "expression_switch_statement",
                                "type_switch_statement", "select_statement",
                                "expression_case", "type_case", "communication_case"}),
        loop_nodes=frozenset({"for_statement", "range_clause"}),
        try_nodes=frozenset({"defer_statement"}),
        catch_nodes=frozenset({"defer_statement"}),
        throw_nodes=frozenset({"go_statement"}),
        block_nodes=frozenset({"block"}),
        param_nodes=frozenset({"parameter_list"}),
        call_nodes=frozenset({"call_expression"}),
        import_nodes=frozenset({"import_declaration", "import_spec"}),
        boolean_op_nodes=frozenset({"binary_expression"}),
        number_nodes=frozenset({"int_literal", "float_literal"}),
        extra={"error_check": "if_statement"},
    ),
    "java": LangSpec(
        name="java",
        function_nodes=frozenset({"method_declaration", "constructor_declaration",
                                  "lambda_expression"}),
        class_nodes=frozenset({"class_declaration", "interface_declaration",
                               "enum_declaration", "record_declaration"}),
        branch_nodes=_C_FAMILY_BRANCH | frozenset({"switch_expression", "switch_block_statement_group"}),
        loop_nodes=_C_FAMILY_LOOP,
        try_nodes=frozenset({"try_statement", "try_with_resources_statement"}),
        catch_nodes=frozenset({"catch_clause"}),
        throw_nodes=frozenset({"throw_statement"}),
        block_nodes=frozenset({"block", "class_body"}),
        param_nodes=frozenset({"formal_parameters"}),
        call_nodes=frozenset({"method_invocation", "object_creation_expression"}),
        import_nodes=frozenset({"import_declaration"}),
        boolean_op_nodes=frozenset({"binary_expression"}),
        ternary_nodes=frozenset({"ternary_expression"}),
        number_nodes=frozenset({"decimal_integer_literal", "decimal_floating_point_literal"}),
    ),
    "ruby": LangSpec(
        name="ruby",
        function_nodes=frozenset({"method", "singleton_method", "lambda", "block", "do_block"}),
        class_nodes=frozenset({"class", "module"}),
        branch_nodes=frozenset({"if", "elsif", "unless", "case", "when", "conditional"}),
        loop_nodes=frozenset({"while", "until", "for", "do_block"}),
        try_nodes=frozenset({"begin", "begin_block"}),
        catch_nodes=frozenset({"rescue"}),
        throw_nodes=frozenset({"raise"}),
        block_nodes=frozenset({"body_statement", "then"}),
        param_nodes=frozenset({"method_parameters", "block_parameters"}),
        call_nodes=frozenset({"call", "method_call"}),
        import_nodes=frozenset({"call"}),
        boolean_op_nodes=frozenset({"binary"}),
        ternary_nodes=frozenset({"conditional"}),
        number_nodes=frozenset({"integer", "float"}),
        global_markers=frozenset({"global_variable"}),
    ),
    "rust": LangSpec(
        name="rust",
        function_nodes=frozenset({"function_item", "closure_expression"}),
        class_nodes=frozenset({"struct_item", "enum_item", "trait_item", "impl_item"}),
        branch_nodes=frozenset({"if_expression", "match_expression", "match_arm",
                                "if_let_expression"}),
        loop_nodes=frozenset({"for_expression", "while_expression", "loop_expression"}),
        try_nodes=frozenset({"try_expression"}),
        catch_nodes=frozenset({"match_expression"}),
        throw_nodes=frozenset({"macro_invocation"}),
        block_nodes=frozenset({"block"}),
        param_nodes=frozenset({"parameters", "closure_parameters"}),
        call_nodes=frozenset({"call_expression", "macro_invocation"}),
        import_nodes=frozenset({"use_declaration"}),
        boolean_op_nodes=frozenset({"binary_expression"}),
        number_nodes=frozenset({"integer_literal", "float_literal"}),
        global_markers=frozenset({"static_item"}),
    ),
    "c": LangSpec(
        name="c",
        function_nodes=frozenset({"function_definition"}),
        class_nodes=frozenset({"struct_specifier", "union_specifier", "enum_specifier"}),
        branch_nodes=_C_FAMILY_BRANCH,
        loop_nodes=_C_FAMILY_LOOP,
        try_nodes=frozenset(),
        catch_nodes=frozenset(),
        throw_nodes=frozenset(),
        block_nodes=frozenset({"compound_statement"}),
        param_nodes=frozenset({"parameter_list"}),
        call_nodes=frozenset({"call_expression"}),
        import_nodes=frozenset({"preproc_include"}),
        boolean_op_nodes=frozenset({"binary_expression"}),
        ternary_nodes=frozenset({"conditional_expression"}),
        number_nodes=frozenset({"number_literal"}),
    ),
    "php": LangSpec(
        name="php",
        function_nodes=frozenset({"function_definition", "method_declaration",
                                  "anonymous_function_creation_expression", "arrow_function"}),
        class_nodes=frozenset({"class_declaration", "interface_declaration", "trait_declaration"}),
        branch_nodes=frozenset({"if_statement", "switch_statement", "case_statement",
                                "else_if_clause", "conditional_expression", "match_expression"}),
        loop_nodes=frozenset({"for_statement", "while_statement", "do_statement",
                              "foreach_statement"}),
        try_nodes=frozenset({"try_statement"}),
        catch_nodes=frozenset({"catch_clause"}),
        throw_nodes=frozenset({"throw_expression", "throw_statement"}),
        block_nodes=frozenset({"compound_statement"}),
        param_nodes=frozenset({"formal_parameters"}),
        call_nodes=frozenset({"function_call_expression", "member_call_expression",
                              "object_creation_expression"}),
        import_nodes=frozenset({"namespace_use_declaration", "require_expression",
                                "include_expression"}),
        boolean_op_nodes=frozenset({"binary_expression"}),
        number_nodes=frozenset({"integer", "float"}),
        global_markers=frozenset({"global_declaration"}),
    ),
    "kotlin": LangSpec(
        name="kotlin",
        function_nodes=frozenset({"function_declaration", "anonymous_function", "lambda_literal"}),
        class_nodes=frozenset({"class_declaration", "object_declaration"}),
        branch_nodes=frozenset({"if_expression", "when_expression", "when_entry"}),
        loop_nodes=frozenset({"for_statement", "while_statement", "do_while_statement"}),
        try_nodes=frozenset({"try_expression"}),
        catch_nodes=frozenset({"catch_block"}),
        throw_nodes=frozenset({"jump_expression"}),
        block_nodes=frozenset({"statements", "class_body", "function_body"}),
        param_nodes=frozenset({"function_value_parameters"}),
        call_nodes=frozenset({"call_expression"}),
        import_nodes=frozenset({"import_header"}),
        boolean_op_nodes=frozenset({"conjunction_expression", "disjunction_expression"}),
        number_nodes=frozenset({"integer_literal", "real_literal"}),
    ),
    "swift": LangSpec(
        name="swift",
        function_nodes=frozenset({"function_declaration", "lambda_literal", "init_declaration"}),
        class_nodes=frozenset({"class_declaration", "protocol_declaration"}),
        branch_nodes=frozenset({"if_statement", "switch_statement", "switch_entry",
                                "guard_statement", "ternary_expression"}),
        loop_nodes=frozenset({"for_statement", "while_statement", "repeat_while_statement"}),
        try_nodes=frozenset({"do_statement"}),
        catch_nodes=frozenset({"catch_block", "catch_clause"}),
        throw_nodes=frozenset({"throw_statement"}),
        block_nodes=frozenset({"statements", "code_block"}),
        param_nodes=frozenset({"parameter"}),
        call_nodes=frozenset({"call_expression"}),
        import_nodes=frozenset({"import_declaration"}),
        number_nodes=frozenset({"integer_literal", "real_literal"}),
    ),
    "bash": LangSpec(
        name="bash",
        function_nodes=frozenset({"function_definition"}),
        branch_nodes=frozenset({"if_statement", "case_statement", "case_item",
                                "elif_clause", "ternary_expression"}),
        loop_nodes=frozenset({"for_statement", "while_statement", "c_style_for_statement"}),
        block_nodes=frozenset({"compound_statement", "do_group"}),
        call_nodes=frozenset({"command"}),
        comment_nodes=frozenset({"comment"}),
        number_nodes=frozenset({"number"}),
    ),
    "lua": LangSpec(
        name="lua",
        function_nodes=frozenset({"function_declaration", "function_definition"}),
        branch_nodes=frozenset({"if_statement", "elseif_statement"}),
        loop_nodes=frozenset({"for_statement", "while_statement", "repeat_statement"}),
        block_nodes=frozenset({"block"}),
        param_nodes=frozenset({"parameters"}),
        call_nodes=frozenset({"function_call"}),
        number_nodes=frozenset({"number"}),
    ),
    "scala": LangSpec(
        name="scala",
        function_nodes=frozenset({"function_definition", "lambda_expression"}),
        class_nodes=frozenset({"class_definition", "object_definition", "trait_definition"}),
        branch_nodes=frozenset({"if_expression", "match_expression", "case_clause"}),
        loop_nodes=frozenset({"for_expression", "while_expression"}),
        try_nodes=frozenset({"try_expression"}),
        catch_nodes=frozenset({"catch_clause"}),
        throw_nodes=frozenset({"throw_expression"}),
        block_nodes=frozenset({"block", "template_body"}),
        param_nodes=frozenset({"parameters"}),
        call_nodes=frozenset({"call_expression"}),
        import_nodes=frozenset({"import_declaration"}),
        number_nodes=frozenset({"integer_literal", "floating_point_literal"}),
    ),
    "dart": LangSpec(
        name="dart",
        function_nodes=frozenset({"function_signature", "method_signature",
                                  "function_expression", "lambda_expression"}),
        class_nodes=frozenset({"class_definition", "mixin_declaration"}),
        branch_nodes=_C_FAMILY_BRANCH,
        loop_nodes=_C_FAMILY_LOOP,
        try_nodes=frozenset({"try_statement"}),
        catch_nodes=frozenset({"catch_clause", "on_part"}),
        throw_nodes=frozenset({"throw_expression"}),
        block_nodes=frozenset({"block", "class_body"}),
        param_nodes=frozenset({"formal_parameter_list"}),
        call_nodes=frozenset({"selector", "new_expression"}),
        import_nodes=frozenset({"import_or_export"}),
        number_nodes=frozenset({"decimal_integer_literal", "decimal_floating_point_literal"}),
    ),
    "elixir": LangSpec(
        name="elixir",
        function_nodes=frozenset({"call", "anonymous_function"}),
        branch_nodes=frozenset({"call"}),
        loop_nodes=frozenset({"call"}),
        block_nodes=frozenset({"do_block", "block"}),
        call_nodes=frozenset({"call"}),
        number_nodes=frozenset({"integer", "float"}),
    ),
}

# Aliases: grammars that share a vocabulary with an existing spec.
_ALIASES = {
    "typescript": "javascript",
    "tsx": "javascript",
    "jsx": "javascript",
    "cpp": "c",
    "c_sharp": "java",
    "csharp": "java",
    "objc": "c",
    "r": "python",
    "perl": "python",
}


def get_spec(language: str) -> LangSpec | None:
    """Return the vocabulary for a language, following aliases."""
    if language in SPECS:
        return SPECS[language]
    alias = _ALIASES.get(language)
    if alias:
        base = SPECS.get(alias)
        if base:
            # Preserve the reported language name while reusing the vocabulary.
            return LangSpec(**{**base.__dict__, "name": language})
    return None


def cpp_spec_fixup() -> None:
    """C++ shares C's vocabulary but adds classes and exceptions."""
    c = SPECS["c"]
    SPECS["cpp"] = LangSpec(
        **{
            **c.__dict__,
            "name": "cpp",
            "class_nodes": c.class_nodes | frozenset({"class_specifier"}),
            "function_nodes": c.function_nodes | frozenset({"lambda_expression"}),
            "try_nodes": frozenset({"try_statement"}),
            "catch_nodes": frozenset({"catch_clause"}),
            "throw_nodes": frozenset({"throw_statement"}),
            "call_nodes": frozenset({"call_expression", "new_expression"}),
            "import_nodes": frozenset({"preproc_include", "using_declaration"}),
        }
    )


cpp_spec_fixup()
