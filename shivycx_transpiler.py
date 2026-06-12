#!/usr/bin/env python3
"""ShivyCX Python-to-C transpiler.

Translates annotated ShivyC compiler modules into C. See the architectural
blueprint in docs/TRANSPILE.md for the phased roadmap.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

SKIP_METHOD_TAGS = frozenset({"pragma: no cover", "transpiler: skip"})
TUPLE_RETURN_ALIASES: Dict[str, Dict[str, Tuple[str, List[str]]]] = {
    "lexer_core": {
        "tokenize_line": ("TokenizeLineResult", ["tokens", "in_comment"]),
        "tokenize_text_line": ("TokenizeLineResult", ["tokens", "in_comment"]),
        "read_string": ("ReadStringResult", ["chars", "end"]),
        "read_include_filename": ("ReadIncludeResult", ["filename", "end"]),
    },
    "parser_core": {
        "parse_root": ("ParseRootResult", ["node", "index"]),
    },
    "parser_declaration": {
        "parse_declarator": ("ParseDeclaratorResult", ["node", "index"]),
        "parse_decls_inits": ("ParseDeclsInitsResult", ["node", "index"]),
        "parse_declaration": ("ParseDeclarationResult", ["node", "index"]),
        "parse_func_definition": ("ParseDeclarationResult", ["node", "index"]),
        "_find_const": ("FindConstResult", ["has_const", "index"]),
        "parse_initializer": ("ParseInitializerResult", ["node", "index"]),
        "parse_init_list": ("ParseInitListResult", ["node", "index"]),
        "parse_decl_specifiers": ("ParseSpecQualListResult", ["specs", "index"]),
        "parse_spec_qual_list": ("ParseSpecQualListResult", ["specs", "index"]),
        "parse_abstract_declarator": ("ParseAbstractDeclaratorResult", ["node", "index"]),
    },
    "parser_expression": {
        "parse_expression": ("ParseExpressionResult", ["node", "index"]),
        "parse_assignment": ("ParseExpressionResult", ["node", "index"]),
        "parse_conditional": ("ParseExpressionResult", ["node", "index"]),
    },
    "parser_statement": {
        "parse_compound_statement": ("ParseCompoundStatementResult", ["node", "index"]),
        "parse_statement": ("ParseStatementResult", ["node", "index"]),
    },
}
TUPLE_STRUCTS_IN_HEADER = frozenset({
    "ParseDeclaratorResult",
    "ParseDeclsInitsResult",
    "ParseDeclarationResult",
    "FindConstResult",
    "ParseRootResult",
    "ParseExpressionResult",
    "ParseCompoundStatementResult",
    "ParseStatementResult",
    "ParseInitializerResult",
    "ParseInitListResult",
    "ParseSpecQualListResult",
    "ParseAbstractDeclaratorResult",
})
PARSER_HEADER_MODULES = frozenset({"parser_declaration", "parser_core"})
_EXPR_NODE_CLASSES = (
    "MultiExpr", "Number", "String", "Identifier", "ParenExpr", "StmtExpr",
    "Plus", "Minus", "Mult", "Div", "Mod", "RBitShift", "LBitShift",
    "BitAnd", "BitOr", "BitXor", "Equality", "Inequality", "LessThan",
    "GreaterThan", "LessThanOrEq", "GreaterThanOrEq", "BoolAnd", "BoolOr",
    "BoolNot", "Conditional", "Equals", "PlusEquals", "MinusEquals",
    "StarEquals", "DivEquals", "ModEquals", "OrEquals", "AndEquals",
    "XorEquals", "LShiftEquals", "RShiftEquals", "PreIncr", "PostIncr",
    "PreDecr", "PostDecr", "UnaryPlus", "UnaryMinus", "Compl", "AddrOf",
    "Deref", "ArraySubsc", "ObjMember", "ObjPtrMember", "SizeofExpr",
    "SizeofType", "AlignofType", "AlignofExpr", "OffsetofType", "Cast",
    "CompoundLiteral", "FuncCall", "VaStartAddr", "VaArg",
)
_STMT_NODE_CLASSES = (
    "Compound", "EmptyStatement", "ExprStatement", "Return", "Break",
    "Continue", "IfStatement", "WhileStatement", "ForStatement",
    "DoWhileStatement", "SwitchStatement", "CaseStatement", "DefaultStatement",
    "LabelStatement", "GotoStatement", "InlineAsm",
)
KNOWN_CLASSES = frozenset({
    "Position", "Range", "Tagged", "Token", "TokenKind",
    "CompilerError", "ErrorCollector", "ParserError", "Node", "Root",
    "DeclNode", "DeclRoot", "DeclPointer", "DeclIdentifier", "DeclArray",
    "DeclFunction", "Declaration", "InitList", "InitDesignator", "InitListEntry",
    "AsmOperand",
    *_EXPR_NODE_CLASSES,
    *_STMT_NODE_CLASSES,
})
NODE_UPCAST_CLASSES = frozenset((
    *_EXPR_NODE_CLASSES,
    *_STMT_NODE_CLASSES,
    "Declaration",
    "Compound",
    "Root",
))
DECL_NODE_SUBCLASSES = frozenset({
    "DeclPointer", "DeclIdentifier", "DeclArray", "DeclRoot", "DeclFunction",
})
STRUCT_FIELD_TYPES: Dict[str, Dict[str, str]] = {
    "Token": {
        "kind": "TokenKind*", "content": "const char*", "rep": "const char*",
        "r": "Range*", "int_content": "IntList*",
    },
    "Range": {"start": "Position*", "end": "Position*"},
    "Tagged": {"c": "const char*", "p": "Position*", "r": "Range*"},
    "Position": {
        "file": "const char*", "full_line": "const char*",
    },
    "ParserError": {"descrip": "const char*", "range": "Range*"},
    "CompilerError": {"descrip": "const char*", "range": "Range*"},
    "Node": {"r": "Range*"},
    "Root": {"nodes": "NodeList*"},
    "DeclNode": {"r": "Range*", "kind": "int"},
    "DeclRoot": {"specs": "TokenList*", "decls": "DeclNodeList*", "kind": "int"},
    "DeclPointer": {"child": "DeclNode*", "is_const": "bool", "kind": "int"},
    "DeclIdentifier": {"identifier": "Token*", "kind": "int"},
    "DeclArray": {"n": "void*", "child": "DeclNode*", "kind": "int"},
    "DeclFunction": {"args": "DeclRootList*", "child": "DeclNode*", "variadic": "bool", "kind": "int"},
    "Declaration": {"node": "DeclRoot*", "body": "Node*"},
    "Compound": {"items": "NodeList*"},
    "Return": {"return_value": "Node*"},
    "IfStatement": {"cond": "Node*", "stat": "Node*", "else_stat": "Node*"},
    "WhileStatement": {"cond": "Node*", "stat": "Node*"},
    "ForStatement": {"first": "Node*", "second": "Node*", "third": "Node*", "stat": "Node*"},
    "DoWhileStatement": {"cond": "Node*", "stat": "Node*"},
    "SwitchStatement": {"cond": "Node*", "stat": "Node*"},
    "CaseStatement": {"expr": "Node*", "stat": "Node*"},
    "DefaultStatement": {"stat": "Node*"},
    "LabelStatement": {"name": "Token*", "stat": "Node*"},
    "GotoStatement": {"name": "Token*"},
    "ExprStatement": {"expr": "Node*"},
    "Identifier": {"identifier": "Token*"},
    "Number": {"number": "Token*"},
    "String": {"chars": "IntList*", "wide": "bool"},
    "FuncCall": {"func": "Node*", "args": "NodeList*"},
    "Compound": {"items": "NodeList*"},
    "InitDesignator": {"tag": "int", "value": "void*"},
}
NODE_FIELD_CASTS: Dict[str, str] = {
    "identifier": "Identifier",
    "number": "Number",
    "chars": "String",
    "func": "FuncCall",
    "expr": "ParenExpr",
    "items": "Compound",
}
IMPORTED_MODULE_GLOBALS = frozenset({"token_kinds"})


class TranspileError(Exception):
    """Raised when the transpiler encounters unsupported Python."""


class Scope:
    def __init__(self, parent: Optional[Scope] = None) -> None:
        self.parent = parent
        self.types: Dict[str, str] = {}
        self.declared: Set[str] = set()

    def get_type(self, name: str) -> Optional[str]:
        if name in self.types:
            return self.types[name]
        if self.parent:
            return self.parent.get_type(name)
        return None

    def declare(self, name: str, c_type: str) -> None:
        self.types[name] = c_type
        self.declared.add(name)

    def child(self) -> Scope:
        return Scope(self)


class ShivyCXTranspiler(ast.NodeVisitor):
    """AST visitor that emits C source from annotated Python."""

    def __init__(self, module_name: str = "module") -> None:
        self.module_name = module_name
        self.indent_level = 0
        self.c_code: List[str] = []
        self.current_class: Optional[str] = None
        self.scope = Scope()
        self.global_types: Dict[str, str] = {}
        self.class_fields: Dict[str, List[Tuple[str, str]]] = {}
        self.imports: List[str] = []
        self.arena_param = False
        self.tuple_structs: Set[str] = set()
        self.tuple_id = 0
        self.tuple_field_types: Dict[str, List[str]] = {}
        self.tuple_field_names: Dict[str, List[str]] = {}
        self.tuple_type_cache: Dict[Tuple[str, ...], str] = {}
        self.function_returns: Dict[str, str] = {}
        self.current_return_type: Optional[str] = None
        self.unpack_id: int = 0
        self.list_types: Set[str] = set()
        self.imported_modules: Set[str] = set()
        self.function_globals: Set[str] = set()
        self.at_module_level = True

        self.type_map = {
            "int": "int",
            "float": "double",
            "str": "const char*",
            "bool": "bool",
            "None": "void",
            "size_t": "size_t",
            "object": "void*",
        }
        self._register_global_tuple_aliases()

    def indent(self) -> str:
        return "    " * self.indent_level

    def emit(self, code: str = "") -> None:
        if code:
            self.c_code.append(f"{self.indent()}{code}")
        else:
            self.c_code.append("")

    def map_type(self, node_annotation: Optional[ast.expr]) -> str:
        if node_annotation is None:
            return "void*"
        if isinstance(node_annotation, ast.Name):
            type_name = node_annotation.id
            if type_name in self.type_map:
                return self.type_map[type_name]
            return f"{type_name}*"
        if isinstance(node_annotation, ast.Constant) and node_annotation.value is None:
            return "void"
        if isinstance(node_annotation, ast.Subscript):
            if isinstance(node_annotation.value, ast.Name) and node_annotation.value.id == "tuple":
                if isinstance(node_annotation.slice, ast.Tuple):
                    field_types = tuple(self.map_type(elt) for elt in node_annotation.slice.elts)
                    if field_types in self.tuple_type_cache:
                        return self.tuple_type_cache[field_types]
                    self.tuple_id += 1
                    name = f"Tuple_{len(field_types)}_{self.tuple_id}"
                    self.tuple_structs.add(name)
                    self.tuple_field_types[name] = list(field_types)
                    self.tuple_type_cache[field_types] = name
                    return name
            if isinstance(node_annotation.slice, ast.Subscript):
                if (
                    isinstance(node_annotation.slice.value, ast.Name)
                    and node_annotation.slice.value.id == "dict"
                ):
                    elem = self.map_type(node_annotation.slice)
                    elem_base = elem.replace("*", "")
                    self.list_types.add(elem_base)
                    return f"{elem_base}List*"
                inner_list = self.map_type(node_annotation.slice)
                inner_base = inner_list.replace("List*", "")
                self.list_types.add(f"{inner_base}ListList")
                return f"{inner_base}ListList*"
            if (
                isinstance(node_annotation.value, ast.Name)
                and node_annotation.value.id == "dict"
                and isinstance(node_annotation.slice, ast.Tuple)
                and len(node_annotation.slice.elts) == 2
            ):
                key_t = self.map_type(node_annotation.slice.elts[0])
                val_t = self.map_type(node_annotation.slice.elts[1])
                if key_t == "const char*" and val_t == "bool":
                    self.list_types.add("StrBoolMap")
                    return "StrBoolMap*"
            if isinstance(node_annotation.value, ast.Name) and node_annotation.value.id in ("list", "List"):
                if isinstance(node_annotation.slice, ast.Name) and node_annotation.slice.id == "str":
                    self.list_types.add("Str")
                    return "StrList*"
                if isinstance(node_annotation.slice, ast.Name) and node_annotation.slice.id == "int":
                    self.list_types.add("Int")
                    return "IntList*"
                elem = self.map_type(node_annotation.slice)
                elem_base = elem.replace("*", "")
                self.list_types.add(elem_base)
                return f"{elem_base}List*"
        if isinstance(node_annotation, ast.Tuple):
            parts = [self.map_type(elt) for elt in node_annotation.elts]
            return f"Tuple_{len(parts)}"
        if isinstance(node_annotation, ast.BinOp) and isinstance(node_annotation.op, ast.BitOr):
            left = self.map_type(node_annotation.left)
            right = self.map_type(node_annotation.right)
            if right in ("void", "None"):
                return left
            if left in ("void", "None"):
                return right
            return left
        return "void*"

    def class_from_expr(self, node: ast.expr) -> Optional[str]:
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "parser_utils" and node.attr == "symbols":
                return "SimpleSymbolTable"
        if isinstance(node, ast.Name):
            c_type = self.scope.get_type(node.id) or self.global_types.get(node.id)
            if c_type:
                return c_type.rstrip("*")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            return node.func.id
        return None

    def parser_utils_global(self, attr: str) -> Optional[str]:
        if attr in ("tokens", "symbols", "shivycx_pending_parser_error", "best_error"):
            return attr
        return None

    def reset_function_scope(self) -> None:
        parent = Scope()
        parent.types = dict(self.global_types)
        parent.declared = set(self.global_types)
        self.scope = parent

    def visit_Module(self, node: ast.Module) -> None:
        self.emit("/* Automatically generated by ShivyCX Transpiler */")
        self.emit(f"/* Source module: {self.module_name} */")
        self.emit("")
        self.emit("#include <stdio.h>")
        self.emit("#include <stdlib.h>")
        self.emit("#include <stdbool.h>")
        self.emit("#include <string.h>")
        self.emit("#include <ctype.h>")
        self.emit('#include "shivycx_runtime.h"')
        self.emit('#include "errors_core.h"')
        if self.module_name in ("lexer_core", "token_kinds"):
            self.emit('#include "tokens.h"')
        if self.module_name == "token_kinds":
            self.emit('#include "token_kinds.h"')
        if self.module_name == "lexer_core":
            self.emit('#include "regex_helpers.h"')
            self.emit('#include "token_kinds.h"')
        if self.module_name == "parser_utils":
            self.emit('#include "tokens.h"')
            self.emit('#include "token_kinds.h"')
        if self.module_name == "decl_nodes":
            self.emit('#include "tokens.h"')
            self.emit('#include "decl_nodes.h"')
        if self.module_name == "expr_nodes":
            self.emit('#include "decl_nodes.h"')
            self.emit('#include "tree_nodes.h"')
            self.emit('#include "expr_nodes.h"')
        if self.module_name == "parser_declaration":
            self.emit('#include "tokens.h"')
            self.emit('#include "token_kinds.h"')
            self.emit('#include "decl_nodes.h"')
            self.emit('#include "parser_utils.h"')
            self.emit('#include "tree_nodes.h"')
            self.emit('#include "parser_expression.h"')
            self.emit('#include "parser_statement.h"')
            self.emit('#include "parser_declaration.h"')
        if self.module_name == "parser_expression":
            self.emit('#include "tokens.h"')
            self.emit('#include "token_kinds.h"')
            self.emit('#include "decl_nodes.h"')
            self.emit('#include "expr_nodes.h"')
            self.emit('#include "parser_utils.h"')
            self.emit('#include "tree_nodes.h"')
            self.emit('#include "parser_declaration.h"')
            self.emit('#include "parser_statement.h"')
            self.emit('#include "parser_expression.h"')
        if self.module_name == "parser_statement":
            self.emit('#include "tokens.h"')
            self.emit('#include "token_kinds.h"')
            self.emit('#include "decl_nodes.h"')
            self.emit('#include "expr_nodes.h"')
            self.emit('#include "parser_utils.h"')
            self.emit('#include "tree_nodes.h"')
            self.emit('#include "parser_declaration.h"')
            self.emit('#include "parser_expression.h"')
            self.emit('#include "parser_statement.h"')
        if self.module_name == "parser_core":
            self.emit('#include "tokens.h"')
            self.emit('#include "token_kinds.h"')
            self.emit('#include "decl_nodes.h"')
            self.emit('#include "expr_nodes.h"')
            self.emit('#include "parser_utils.h"')
            self.emit('#include "parser_declaration.h"')
            self.emit('#include "parser_expression.h"')
            self.emit('#include "parser_statement.h"')
            self.emit('#include "tree_nodes.h"')
            self.emit('#include "parser_core.h"')
        if self.module_name == "tree_nodes":
            self.emit('#include "decl_nodes.h"')
            self.emit('#include "tree_nodes.h"')
        if self.module_name in ("lexer_core", "token_kinds"):
            self.emit('#include "shivycx_exceptions.h"')
        self.emit("")

        self.prescan_module(node)
        self.emit_list_typedefs()

        for item in node.body:
            if isinstance(item, (ast.Import, ast.ImportFrom)):
                self.visit(item)
            elif isinstance(item, ast.ClassDef):
                self.visit(item)

        self.emit_forward_declarations(node)

        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                self.at_module_level = False
                self.visit(item)
            elif isinstance(item, (ast.Assign, ast.AnnAssign)):
                self.at_module_level = True
                self.visit(item)

    def prescan_module(self, node: ast.Module) -> None:
        for item in node.body:
            if isinstance(item, ast.ClassDef):
                for sub in item.body:
                    if isinstance(sub, ast.FunctionDef):
                        self.prescan_function(sub)
            elif isinstance(item, ast.FunctionDef):
                self.prescan_function(item)
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                c_type = self.map_type(item.annotation)
                self.global_types[item.target.id] = c_type

    def _register_global_tuple_aliases(self) -> None:
        for module_aliases in TUPLE_RETURN_ALIASES.values():
            for func_name, (type_name, _field_names) in module_aliases.items():
                self.function_returns[func_name] = type_name

    def register_tuple_alias(
        self,
        name: str,
        field_names: List[str],
        field_types: List[str],
    ) -> None:
        key = tuple(field_types)
        self.tuple_structs.add(name)
        self.tuple_field_types[name] = field_types
        self.tuple_field_names[name] = field_names
        if key not in self.tuple_type_cache:
            self.tuple_type_cache[key] = name

    def emit_forward_declarations(self, node: ast.Module) -> None:
        decls: List[str] = []
        for item in node.body:
            if not isinstance(item, ast.FunctionDef):
                continue
            doc = ast.get_docstring(item)
            if doc and (doc.strip() in SKIP_METHOD_TAGS or "transpiler: skip" in doc):
                continue
            if item.returns:
                ret = self.function_returns.get(item.name) or self.map_type(item.returns)
            else:
                ret = "void"
            params = self.format_params(item, include_self=False)
            prefix = "static " if item.name.startswith("_") else ""
            decls.append(f"{prefix}{ret} {item.name}({', '.join(params)});")
        if decls:
            self.emit("/* Forward declarations */")
            for decl in decls:
                self.emit(decl)
            self.emit("")

    def tuple_field_types_for_call(self, value: ast.expr) -> List[str]:
        tpl_type = "Tuple_0"
        if isinstance(value, ast.Call):
            func_name: Optional[str] = None
            if isinstance(value.func, ast.Name):
                func_name = value.func.id
            elif isinstance(value.func, ast.Attribute):
                func_name = value.func.attr
            if func_name:
                tpl_type = self.function_returns.get(func_name, tpl_type)
                if tpl_type == "Tuple_0":
                    for module_aliases in TUPLE_RETURN_ALIASES.values():
                        if func_name in module_aliases:
                            tpl_type = module_aliases[func_name][0]
                            break
        fields = self.tuple_field_types.get(tpl_type, [])
        if fields:
            return fields
        type_fields: Dict[str, List[str]] = {
            "TokenizeLineResult": ["TokenList*", "bool"],
            "ReadStringResult": ["IntList*", "int"],
            "ReadIncludeResult": ["const char*", "int"],
            "ParseRootResult": ["Root*", "int"],
            "ParseDeclaratorResult": ["DeclNode*", "int"],
            "ParseDeclsInitsResult": ["DeclRoot*", "int"],
            "ParseDeclarationResult": ["Declaration*", "int"],
            "FindConstResult": ["bool", "int"],
            "ParseExpressionResult": ["Node*", "int"],
            "ParseCompoundStatementResult": ["Compound*", "int"],
            "ParseStatementResult": ["Node*", "int"],
            "ParseInitializerResult": ["Node*", "int"],
            "ParseInitListResult": ["InitList*", "int"],
            "ParseSpecQualListResult": ["TokenList*", "int"],
            "ParseAbstractDeclaratorResult": ["DeclNode*", "int"],
        }
        return type_fields.get(tpl_type, [])

    def collect_hoisted_locals(self, node: ast.FunctionDef) -> List[Tuple[str, str]]:
        arg_names = {arg.arg for arg in node.args.args}
        seen: Set[str] = set()
        locals_out: List[Tuple[str, str]] = []

        def add_local(name: str, c_type: str) -> None:
            if name in arg_names or name in seen:
                return
            seen.add(name)
            locals_out.append((name, c_type))

        for stmt in ast.walk(node):
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                add_local(stmt.target.id, self.map_type(stmt.annotation))
            elif (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Tuple)
            ):
                fields = self.tuple_field_types_for_call(stmt.value)
                names = None
                if isinstance(stmt.value, ast.Call):
                    func_name = None
                    if isinstance(stmt.value.func, ast.Name):
                        func_name = stmt.value.func.id
                    elif isinstance(stmt.value.func, ast.Attribute):
                        func_name = stmt.value.func.attr
                    if func_name:
                        for module_aliases in TUPLE_RETURN_ALIASES.values():
                            if func_name in module_aliases:
                                names = module_aliases[func_name][1]
                                break
                for i, elt in enumerate(stmt.targets[0].elts):
                    if isinstance(elt, ast.Name):
                        c_type = fields[i] if i < len(fields) else "void*"
                        add_local(elt.id, c_type)
        return locals_out

    def prescan_function(self, node: ast.FunctionDef) -> None:
        if node.returns:
            alias = TUPLE_RETURN_ALIASES.get(self.module_name, {}).get(node.name)
            if (
                alias
                and isinstance(node.returns, ast.Subscript)
                and isinstance(node.returns.slice, ast.Tuple)
            ):
                type_name, field_names = alias
                field_types = [self.map_type(elt) for elt in node.returns.slice.elts]
                self.register_tuple_alias(type_name, field_names, field_types)
                self.function_returns[node.name] = type_name
            else:
                ret = self.map_type(node.returns)
                self.function_returns[node.name] = ret
        for arg in node.args.args:
            if arg.annotation:
                self.map_type(arg.annotation)
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.AnnAssign):
                self.map_type(stmt.annotation)

    def emit_list_typedefs(self) -> None:
        if self.module_name in ("tree_nodes", "decl_nodes", "expr_nodes"):
            return
        if not self.list_types and not self.tuple_structs:
            return
        self.emit("/* List type definitions */")
        plain = sorted(n for n in self.list_types if not n.endswith("ListList"))
        nested = sorted(n for n in self.list_types if n.endswith("ListList"))
        for name in plain:
            if name in ("Int", "int"):
                continue
            elif name == "TokenKind" and self.module_name == "token_kinds":
                continue
            elif name == "TokenKind" and self.module_name in ("lexer_core", "token_kinds", "parser_utils"):
                continue
            elif name == "Token" and self.module_name in ("lexer_core", "parser_utils"):
                continue
            elif name == "Token" and self.module_name in (
                "lexer_core", "parser_utils", "parser_core", "decl_nodes",
                "parser_declaration", "parser_expression", "parser_statement",
            ):
                continue
            elif name == "Node" and self.module_name in (
                "parser_core", "parser_expression", "parser_statement",
            ):
                continue
            elif name == "Token" and self.module_name in ("decl_nodes", "parser_declaration"):
                continue
            elif name == "DeclNode" and self.module_name in (
                "decl_nodes", "parser_declaration", "parser_expression", "parser_statement",
            ):
                continue
            elif name == "InitDesignator" and self.module_name in (
                "decl_nodes", "parser_declaration", "parser_expression",
            ):
                continue
            elif name == "AsmOperand" and self.module_name in (
                "tree_nodes", "parser_statement",
            ):
                continue
            elif name == "DeclRoot" and self.module_name == "parser_declaration":
                continue
            elif name == "DeclRootList" and self.module_name == "parser_declaration":
                continue
            elif name == "CompilerError" and self.module_name == "errors_core":
                continue
            elif name == "StrBoolMap":
                continue
            elif name == "Str":
                if self.module_name in ("lexer_core", "parser_statement", "tree_nodes"):
                    continue
                self.emit("typedef struct { char **data; size_t size; size_t capacity; } StrList;")
                self.emit("static inline void StrList_init(StrList *list) { list->data = NULL; list->size = 0; list->capacity = 0; }")
                self.emit("static inline void StrList_push(StrList *list, const char *item) {")
                self.emit("    if (list->size + 1 > list->capacity) {")
                self.emit("        size_t cap = list->capacity ? list->capacity * 2 : 8;")
                self.emit("        list->data = (char **)realloc(list->data, cap * sizeof(char *));")
                self.emit("        list->capacity = cap;")
                self.emit("    }")
                self.emit("    list->data[list->size++] = (char *)item;")
                self.emit("}")
                self.emit("static inline size_t StrList_len(const StrList *list) { return list->size; }")
                self.emit("static inline const char *StrList_get(const StrList *list, size_t index) { return list->data[index]; }")
                self.emit("static inline void StrList_clear(StrList *list) { list->size = 0; }")
            else:
                self.emit(f"DEFINE_LIST({name}, {name}List)")
                self.emit(f"static inline {name}List* {name}List_slice({name}List *list, size_t start, size_t end) {{")
                self.emit(f"    {name}List *out = ({name}List *)malloc(sizeof({name}List));")
                self.emit(f"    {name}List_init(out);")
                self.emit("    for (size_t i = start; i < end && i < list->size; i++) {")
                self.emit(f"        {name}List_push(out, list->data[i]);")
                self.emit("    }")
                self.emit("    return out;")
                self.emit("}")
        for name in nested:
            inner = name[: -len("ListList")]
            self.emit(f"DEFINE_LIST({inner}List, {name})")
        self.emit("")
        for name in sorted(self.tuple_structs):
            if name in TUPLE_STRUCTS_IN_HEADER:
                continue
            fields_list = self.tuple_field_types.get(name, [])
            field_names = self.tuple_field_names.get(name)
            if field_names and len(field_names) == len(fields_list):
                fields = "; ".join(
                    f"{fields_list[i]} {field_names[i]}" for i in range(len(fields_list))
                )
            else:
                fields = "; ".join(
                    f"{fields_list[i]} f{i}" for i in range(len(fields_list))
                )
            self.emit(f"typedef struct {{ {fields}; }} {name};")
        if self.tuple_structs:
            self.emit("")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(f"import {alias.name}")
            base = alias.asname or alias.name.split(".")[-1]
            if alias.name.endswith("token_kinds") or base == "token_kinds":
                self.imported_modules.add(base)
                self.global_types["symbol_kinds"] = "TokenKindList*"
                self.global_types["keyword_kinds"] = "TokenKindList*"
                for kind in (
                    "bool_kw", "char_kw", "short_kw", "int_kw", "long_kw",
                    "float_kw", "double_kw", "signed_kw", "unsigned_kw", "void_kw",
                    "auto_kw", "register_kw", "static_kw", "extern_kw",
                    "struct_kw", "union_kw", "enum_kw", "const_kw", "volatile_kw",
                    "restrict_kw", "atomic_kw",                     "typedef_kw", "asm_kw",
                    "star", "open_paren", "close_paren", "open_brack", "close_brack",
                    "colon",
                    "open_sq_brack", "close_sq_brack", "comma", "equals", "dots",
                    "dquote", "squote", "pound", "identifier", "string",
                    "char_string", "include_file", "number", "unrecognized",
                    "semicolon",
                ):
                    self.global_types[kind] = "TokenKind*"
            if alias.name.endswith("errors_core") or base == "errors_core":
                self.imported_modules.add(base)
                self.global_types["error_collector"] = "ErrorCollector*"
                self.global_types["shivycx_pending_error"] = "CompilerError*"
            if alias.name.endswith("parser_utils") or base == "parser_utils":
                self.imported_modules.add("parser_utils")
                self.global_types["tokens"] = "TokenList*"
                self.global_types["best_error"] = "ParserError*"
                self.global_types["symbols"] = "SimpleSymbolTable*"
                self.global_types["shivycx_pending_parser_error"] = "ParserError*"
                self.global_types["cur_func_name"] = "const char*"
            if alias.name.endswith("parser_statement") or base == "parser_statement":
                self.imported_modules.add("parser_statement")
            if alias.name.endswith("parser_expression") or base == "parser_expression":
                self.imported_modules.add("parser_expression")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "__future__":
            return
        names = ", ".join(a.name for a in node.names)
        self.imports.append(f"from {node.module} import {names}")
        if node.module and "token_kinds" in node.module:
            for alias in node.names:
                name = alias.asname or alias.name
                self.imported_modules.add(name)
            self.global_types["symbol_kinds"] = "TokenKindList*"
            self.global_types["keyword_kinds"] = "TokenKindList*"
            for kind in (
                "bool_kw", "char_kw", "short_kw", "int_kw", "long_kw",
                "float_kw", "double_kw", "signed_kw", "unsigned_kw", "void_kw",
                "auto_kw", "register_kw", "static_kw", "extern_kw",
                "struct_kw", "union_kw", "enum_kw", "const_kw", "volatile_kw",
                "restrict_kw", "atomic_kw",                     "typedef_kw", "asm_kw",
                    "star", "open_paren", "close_paren", "open_brack", "close_brack",
                    "colon",
                "open_sq_brack", "close_sq_brack", "comma", "equals",
                "dquote", "squote", "pound", "identifier", "string",
                "char_string", "include_file", "number", "unrecognized",
                "semicolon",
            ):
                self.global_types[kind] = "TokenKind*"
        if node.module and "parser_utils" in node.module:
            for alias in node.names:
                name = alias.asname or alias.name
                if name == "symbols":
                    self.global_types["symbols"] = "SimpleSymbolTable*"
                if name == "tokens":
                    self.global_types["tokens"] = "TokenList*"
                if name == "shivycx_pending_parser_error":
                    self.global_types["shivycx_pending_parser_error"] = "ParserError*"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        class_name = node.name
        self.current_class = class_name
        attributes: List[Tuple[str, str]] = []

        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                for stmt in item.body:
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Attribute):
                        if isinstance(stmt.target.value, ast.Name) and stmt.target.value.id == "self":
                            attributes.append((self.map_type(stmt.annotation), stmt.target.attr))

        self.class_fields[class_name] = attributes
        if self.module_name not in ("errors_core", "tree_nodes", "decl_nodes", "expr_nodes"):
            self.emit(f"typedef struct {class_name} {class_name};")
            self.emit(f"struct {class_name} {{")
            self.indent_level += 1
            for c_type, attr_name in attributes:
                self.emit(f"{c_type} {attr_name};")
            self.indent_level -= 1
            self.emit("};")
            self.emit("")

        init_method = next(
            (m for m in node.body if isinstance(m, ast.FunctionDef) and m.name == "__init__"),
            None,
        )
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name != "__init__":
                self.visit(item)
        if init_method:
            self.generate_constructor(class_name, init_method)

        self.current_class = None

    def generate_constructor(self, class_name: str, init_node: ast.FunctionDef) -> None:
        self.reset_function_scope()
        self.scope.declare("self", f"{class_name}*")
        for arg in init_node.args.args:
            if arg.arg != "self" and arg.annotation:
                self.scope.declare(arg.arg, self.map_type(arg.annotation))
        params = self.format_params(init_node, include_self=False)
        param_list = ", ".join(params)

        self.emit(f"{class_name}* {class_name}_new({param_list}) {{")
        self.indent_level += 1
        self.emit(f"{class_name}* self = ({class_name}*)malloc(sizeof({class_name}));")

        for stmt in init_node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Attribute):
                if isinstance(stmt.target.value, ast.Name) and stmt.target.value.id == "self":
                    attr = stmt.target.attr
                    if stmt.value:
                        if isinstance(stmt.value, ast.List) and len(stmt.value.elts) == 0:
                            c_type = self.map_type(stmt.annotation)
                            base = c_type.replace("*", "")
                            self.emit(f"self->{attr} = ({c_type})malloc(sizeof({base}));")
                            self.emit(f"{base}_init(self->{attr});")
                        elif isinstance(stmt.value, ast.Dict) and len(stmt.value.keys) == 0:
                            self.emit(f"self->{attr} = StrBoolMap_new();")
                        else:
                            self.emit(f"self->{attr} = {self.to_c_expr(stmt.value)};")
                    elif stmt.annotation:
                        c_type = self.map_type(stmt.annotation)
                        if c_type == "bool":
                            self.emit(f"self->{attr} = false;")
                        elif c_type.endswith("*"):
                            self.emit(f"self->{attr} = NULL;")
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                        if target.value.id == "self":
                            self.emit(f"self->{target.attr} = {self.to_c_expr(stmt.value)};")
            else:
                self.visit(stmt)

        self.emit("return self;")
        self.indent_level -= 1
        self.emit("}")
        self.emit("")

    def format_params(self, node: ast.FunctionDef, include_self: bool = True) -> List[str]:
        params: List[str] = []
        defaults_offset = len(node.args.args) - len(node.args.defaults)
        for i, arg in enumerate(node.args.args):
            if arg.arg == "self":
                if include_self and self.current_class:
                    params.append(f"{self.current_class}* self")
                continue
            c_type = self.map_type(arg.annotation)
            default_idx = i - defaults_offset
            if default_idx >= 0:
                default = node.args.defaults[default_idx]
                if isinstance(default, ast.Constant) and default.value is None:
                    c_type = f"{c_type.rstrip('*')}*"
            params.append(f"{c_type} {arg.arg}")
        if not params and not include_self:
            params.append("void")
        return params

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        doc = ast.get_docstring(node)
        if doc and doc.strip() in SKIP_METHOD_TAGS:
            return
        if doc and "transpiler: skip" in doc:
            return

        self.reset_function_scope()
        self.function_globals = set()
        self.unpack_id = 0
        self.at_module_level = False
        func_name = node.name
        if self.current_class:
            func_name = f"{self.current_class}_{func_name}"

        params = self.format_params(node)
        if node.returns:
            return_type = self.function_returns.get(node.name) or self.map_type(node.returns)
        else:
            return_type = "void"
        self.current_return_type = return_type

        prefix = "static " if node.name.startswith("_") and not self.current_class else ""
        self.emit(f"{prefix}{return_type} {func_name}({', '.join(params)}) {{")
        self.indent_level += 1

        for arg in node.args.args:
            if arg.arg == "self" and self.current_class:
                self.scope.declare("self", f"{self.current_class}*")
            elif arg.arg != "self" and arg.annotation:
                self.scope.declare(arg.arg, self.map_type(arg.annotation))

        used_names: Set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                used_names.add(child.id)
        for arg in node.args.args:
            if arg.arg != "self" and arg.arg not in used_names:
                self.emit(f"(void){arg.arg};")

        for name, c_type in self.collect_hoisted_locals(node):
            self.scope.declare(name, c_type)
            if c_type.endswith("*"):
                self.emit(f"{c_type} {name} = NULL;")
            elif c_type == "bool":
                self.emit(f"bool {name} = false;")
            else:
                self.emit(f"{c_type} {name};")

        for stmt in node.body:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                if isinstance(stmt.value.value, str):
                    if stmt.value.value.strip() in SKIP_METHOD_TAGS:
                        continue
                    if stmt is node.body[0] and stmt.value.value == doc:
                        continue
            self.visit(stmt)

        self.indent_level -= 1
        self.emit("}")
        self.emit("")

    def visit_Global(self, node: ast.Global) -> None:
        for name in node.names:
            self.function_globals.add(name)

    def emit_empty_list(self, var_name: str, c_type: str) -> None:
        base = c_type.replace("*", "")
        self.emit(f"{c_type} {var_name} = ({c_type})malloc(sizeof({base}));")
        self.emit(f"{base}_init({var_name});")

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and self.at_module_level:
            var_name = node.target.id
            c_type = self.map_type(node.annotation)
            self.global_types[var_name] = c_type
            self.scope.declare(var_name, c_type)
            if node.value:
                if isinstance(node.value, ast.List) and len(node.value.elts) == 0:
                    self.emit_empty_list(var_name, c_type)
                elif isinstance(node.value, ast.Constant) and node.value.value is None:
                    self.emit(f"{c_type} {var_name} = NULL;")
                else:
                    val = self.to_c_expr(node.value)
                    if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
                        cls = node.value.func.id
                        if cls in self.class_fields or cls in KNOWN_CLASSES:
                            val = self.format_constructor_call(cls, node.value)
                    self.emit(f"{c_type} {var_name} = {val};")
            elif c_type.endswith("*"):
                self.emit(f"{c_type} {var_name} = NULL;")
            else:
                self.emit(f"{c_type} {var_name};")
            return
        if isinstance(node.target, ast.Name):
            var_name = node.target.id
            c_type = self.map_type(node.annotation)
            is_module = self.at_module_level
            if is_module:
                self.global_types[var_name] = c_type
            if var_name not in self.scope.declared:
                self.scope.declare(var_name, c_type)
                if node.value:
                    if isinstance(node.value, ast.List) and len(node.value.elts) == 0:
                        if self.at_module_level:
                            self.emit_empty_list(var_name, c_type)
                        else:
                            self.emit_empty_list(var_name, c_type)
                    elif isinstance(node.value, ast.Dict) and len(node.value.keys) == 0:
                        self.emit(f"{c_type} {var_name} = StrBoolMap_new();")
                    elif isinstance(node.value, ast.Constant) and node.value.value is None:
                        self.emit(f"{c_type} {var_name} = NULL;")
                    else:
                        val = self.to_c_expr(node.value)
                        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
                            cls = node.value.func.id
                            if cls in self.class_fields or cls in KNOWN_CLASSES:
                                val = self.format_constructor_call(cls, node.value)
                        val_type = c_type
                        if (
                            c_type == "const char*"
                            and isinstance(node.value, ast.Subscript)
                            and isinstance(node.value.value, ast.Name)
                            and (self.scope.get_type(node.value.value.id) or "") == "const char*"
                        ):
                            val_type = "char"
                        if (
                            c_type == "Node*"
                            and isinstance(node.value, ast.Call)
                            and isinstance(node.value.func, ast.Name)
                            and node.value.func.id in NODE_UPCAST_CLASSES
                        ):
                            val = f"(Node *)({val})"
                        self.emit(f"{val_type} {var_name} = {val};")
                elif self.at_module_level and c_type.endswith("*"):
                    self.emit(f"{c_type} {var_name} = NULL;")
                else:
                    if c_type.endswith("*"):
                        self.emit(f"{c_type} {var_name} = NULL;")
                    else:
                        self.emit(f"{c_type} {var_name};")
            elif node.value:
                if isinstance(node.value, ast.List) and len(node.value.elts) == 0:
                    base = c_type.replace("*", "")
                    if c_type.endswith("*"):
                        self.emit(
                            f"if (!{var_name}) {{ {var_name} = ({c_type})malloc(sizeof({base})); "
                            f"{base}_init({var_name}); }} else {{ {base}_clear({var_name}); }}"
                        )
                    else:
                        self.emit(f"{base}_clear({var_name});")
                else:
                    val = self.to_c_expr(node.value)
                    if (
                        c_type == "Node*"
                        and isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Name)
                        and node.value.func.id in NODE_UPCAST_CLASSES
                    ):
                        val = f"(Node *)({val})"
                    self.emit(f"{var_name} = {val};")

    def visit_Assign(self, node: ast.Assign) -> None:
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Tuple):
            self.emit_tuple_unpack(node.targets[0], node.value)
            return
        val_str = self.to_c_expr(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                var_name = target.id
                if var_name in self.function_globals:
                    self.emit(f"{var_name} = {val_str};")
                elif var_name not in self.scope.declared:
                    inferred = self.infer_type_from_value(node.value)
                    if self.at_module_level:
                        self.global_types[var_name] = inferred
                    self.scope.declare(var_name, inferred)
                    self.emit(f"{inferred} {var_name} = {val_str};")
                else:
                    declared_type = self.scope.get_type(var_name) or ""
                    if (
                        declared_type == "Node*"
                        and isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Name)
                        and node.value.func.id in NODE_UPCAST_CLASSES
                    ):
                        val_str = f"(Node *)({val_str})"
                    self.emit(f"{var_name} = {val_str};")
            elif isinstance(target, ast.Attribute):
                if (
                    isinstance(target.value, ast.Name)
                    and target.value.id in self.imported_modules
                    and target.attr in self.global_types
                ):
                    self.emit(f"{target.attr} = {val_str};")
                    continue
                if isinstance(node.value, ast.List) and len(node.value.elts) == 0:
                    obj = self.to_c_expr(target.value)
                    op = "->" if self.is_pointer_expr(target.value) else "."
                    list_expr = f"{obj}{op}{target.attr}"
                    base = self.list_base_from_attr(target)
                    if base != "Unknown":
                        self.emit(f"{self.list_op(base, 'clear', list_expr)};")
                        continue
                obj = self.to_c_expr(target.value)
                op = "->" if self.is_pointer_expr(target.value) else "."
                self.emit(f"{obj}{op}{target.attr} = {val_str};")
            elif isinstance(target, ast.Subscript):
                if self.expr_points_to_map(target.value):
                    self.emit(f"{self.subscript_set(target, val_str)};")
                else:
                    base = self.list_base(target.value)
                    if base != "Unknown":
                        self.emit(
                            f"{self.list_op(base, 'set', self.to_c_expr(target.value), self.to_c_expr(target.slice), val_str)};"
                        )
                    else:
                        self.emit(f"{self.subscript_set(target, val_str)};")

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        op_map = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Mod: "%"}
        target = self.to_c_assign_target(node.target)
        val = self.to_c_expr(node.value)
        if isinstance(node.op, ast.Add) and isinstance(node.target, ast.Name):
            c_type = self.scope.get_type(node.target.id) or ""
            if c_type.endswith("List*"):
                base = c_type.replace("List*", "")
                self.emit(f"{self.list_op(base, 'extend', node.target.id, val)};")
                return
        if isinstance(node.op, ast.Add) and isinstance(node.target, ast.Subscript):
            base = self.list_base(node.target.value)
            if base != "Unknown":
                inner = self.to_c_expr(node.target.slice)
                outer = self.to_c_expr(node.target.value)
                self.emit(f"{self.list_op(base, 'extend', self.list_get(node.target.value, inner), val)};")
                return
        self.emit(f"{target} {op_map.get(type(node.op), '+')}={val};")

    def visit_If(self, node: ast.If) -> None:
        self.emit_if_chain(node.test, node.body, node.orelse)

    def emit_if_chain(self, test, body, orelse) -> None:
        self.emit(f"if ({self.to_c_expr(test)}) {{")
        self.indent_level += 1
        for stmt in body:
            self.visit(stmt)
        self.indent_level -= 1

        while len(orelse) == 1 and isinstance(orelse[0], ast.If):
            elif_node = orelse[0]
            self.emit(f"}} else if ({self.to_c_expr(elif_node.test)}) {{")
            self.indent_level += 1
            for stmt in elif_node.body:
                self.visit(stmt)
            self.indent_level -= 1
            orelse = elif_node.orelse
            continue

        if orelse:
            self.emit("} else {")
            self.indent_level += 1
            for stmt in orelse:
                self.visit(stmt)
            self.indent_level -= 1
        self.emit("}")

    def visit_While(self, node: ast.While) -> None:
        if isinstance(node.test, ast.Constant) and node.test.value is True:
            self.emit("while (1) {")
        else:
            self.emit(f"while ({self.to_c_expr(node.test)}) {{")
        self.indent_level += 1
        for stmt in node.body:
            self.visit(stmt)
        self.indent_level -= 1
        self.emit("}")

    def is_reversed_list_iter(self, node: ast.expr) -> bool:
        if not isinstance(node, ast.Subscript):
            return False
        sl = node.slice
        if not isinstance(sl, ast.Slice):
            return False
        if sl.lower is not None or sl.upper is not None:
            return False
        if not isinstance(sl.step, ast.UnaryOp) or not isinstance(sl.step.op, ast.USub):
            return False
        return isinstance(sl.step.operand, ast.Constant) and sl.step.operand.value == 1

    def visit_For(self, node: ast.For) -> None:
        if isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Name):
            if node.iter.func.id == "range":
                self.emit_range_for(node)
                return
            if node.iter.func.id == "enumerate":
                self.emit_enumerate_for(node)
                return
        if self.is_reversed_list_iter(node.iter) and isinstance(node.target, ast.Name):
            var = node.target.id
            seq = node.iter.value
            elem_type = self.list_elem_type(seq)
            self.scope.declare(var, elem_type)
            self.emit(f"for (size_t _ri = {self.list_len(seq)}; _ri > 0; ) {{")
            self.indent_level += 1
            self.emit("_ri--;")
            self.emit(f"{elem_type} {var} = {self.list_get(seq, '_ri')};")
            for stmt in node.body:
                self.visit(stmt)
            self.indent_level -= 1
            self.emit("}")
            return
        if isinstance(node.target, ast.Name):
            var = node.target.id
            elem_type = self.list_elem_type(node.iter)
            self.scope.declare(var, elem_type)
            self.emit(f"for (size_t _i = 0; _i < {self.list_len(node.iter)}; _i++) {{")
            self.indent_level += 1
            self.emit(f"{elem_type} {var} = {self.list_get(node.iter, '_i')};")
            for stmt in node.body:
                self.visit(stmt)
            self.indent_level -= 1
            self.emit("}")
            return
        raise TranspileError(f"unsupported for-loop: {ast.dump(node)}")

    def emit_enumerate_for(self, node: ast.For) -> None:
        if not isinstance(node.target, (ast.Tuple, ast.List)) or len(node.target.elts) != 2:
            raise TranspileError("enumerate() requires 2-element tuple target")
        idx_name = node.target.elts[0].id
        val_name = node.target.elts[1].id
        seq = node.iter.args[0]
        elem_type = self.list_elem_type(seq)
        self.scope.declare(idx_name, "size_t")
        self.scope.declare(val_name, elem_type)
        self.emit(f"for (size_t {idx_name} = 0; {idx_name} < {self.list_len(seq)}; {idx_name}++) {{")
        self.indent_level += 1
        self.emit(f"{elem_type} {val_name} = {self.list_get(seq, idx_name)};")
        for stmt in node.body:
            self.visit(stmt)
        self.indent_level -= 1
        self.emit("}")

    def list_elem_type(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            c_type = self.scope.get_type(node.id) or ""
            if c_type.endswith("ListList*"):
                base = c_type.replace("ListList*", "")
                return f"{base}List*"
            if c_type.endswith("List*"):
                base = c_type.replace("List*", "")
                if base == "Int":
                    return "int"
                return f"{base}*"
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self" and self.current_class:
            for c_type, attr_name in self.class_fields.get(self.current_class, []):
                if attr_name == node.attr and c_type.endswith("List*"):
                    base = c_type.replace("List*", "")
                    return f"{base}*"
        return "void*"

    def map_base(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            c_type = self.scope.get_type(node.id) or ""
            if c_type == "StrBoolMap*":
                return "StrBoolMap"
        if isinstance(node, ast.Subscript):
            return self.map_base(node.value)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self" and self.current_class:
            for c_type, attr_name in self.class_fields.get(self.current_class, []):
                if attr_name == node.attr and c_type == "StrBoolMap*":
                    return "StrBoolMap"
        return "Unknown"

    def emit_tuple_unpack(self, target: ast.Tuple, value: ast.expr) -> None:
        self.unpack_id += 1
        tmp = f"_unpack_tmp_{self.unpack_id}"
        val = self.to_c_expr(value)
        tpl_type = "Tuple_0"
        if isinstance(value, ast.Call):
            func_name: Optional[str] = None
            if isinstance(value.func, ast.Name):
                func_name = value.func.id
            elif isinstance(value.func, ast.Attribute):
                func_name = value.func.attr
            if func_name:
                tpl_type = self.function_returns.get(func_name, tpl_type)
                if tpl_type == "Tuple_0":
                    for module_aliases in TUPLE_RETURN_ALIASES.values():
                        if func_name in module_aliases:
                            tpl_type = module_aliases[func_name][0]
                            break
        fields = self.tuple_field_types.get(tpl_type, [])
        names = self.tuple_field_names.get(tpl_type)
        if not fields:
            for module_aliases in TUPLE_RETURN_ALIASES.values():
                for _fn, (type_name, field_names) in module_aliases.items():
                    if type_name == tpl_type:
                        names = field_names
                        type_fields: Dict[str, List[str]] = {
                            "TokenizeLineResult": ["TokenList*", "bool"],
                            "ReadStringResult": ["IntList*", "int"],
                            "ReadIncludeResult": ["const char*", "int"],
                            "ParseRootResult": ["Root*", "int"],
                            "ParseDeclaratorResult": ["DeclNode*", "int"],
                            "ParseDeclsInitsResult": ["DeclRoot*", "int"],
                            "ParseDeclarationResult": ["Declaration*", "int"],
                            "FindConstResult": ["bool", "int"],
                            "ParseExpressionResult": ["Node*", "int"],
                            "ParseCompoundStatementResult": ["Compound*", "int"],
                            "ParseStatementResult": ["Node*", "int"],
                            "ParseInitializerResult": ["Node*", "int"],
                            "ParseInitListResult": ["InitList*", "int"],
                            "ParseSpecQualListResult": ["TokenList*", "int"],
                            "ParseAbstractDeclaratorResult": ["DeclNode*", "int"],
                        }
                        fields = type_fields.get(type_name, ["void*"] * len(field_names))
                        break
                if fields:
                    break
        self.emit(f"{tpl_type} {tmp} = {val};")
        for i, elt in enumerate(target.elts):
            if isinstance(elt, ast.Name):
                c_type = fields[i] if i < len(fields) else "void*"
                field = names[i] if names and i < len(names) else f"f{i}"
                if elt.id not in self.scope.declared:
                    self.scope.declare(elt.id, c_type)
                    rhs = f"{tmp}.{field}"
                    if c_type.endswith("*") and tpl_type != c_type:
                        rhs = f"({c_type})({rhs})"
                    self.emit(f"{c_type} {elt.id} = {rhs};")
                else:
                    rhs = f"{tmp}.{field}"
                    declared_type = self.scope.get_type(elt.id) or ""
                    if declared_type.endswith("*") and tpl_type != declared_type:
                        rhs = f"({declared_type})({rhs})"
                    self.emit(f"{elt.id} = {rhs};")

    def emit_range_for(self, node: ast.For) -> None:
        if not isinstance(node.target, ast.Name):
            raise TranspileError("range() for target must be a simple name")
        var = node.target.id
        args = node.iter.args
        if len(args) == 1:
            start, end, step = "0", self.to_c_expr(args[0]), "1"
        elif len(args) == 2:
            start, end = self.to_c_expr(args[0]), self.to_c_expr(args[1])
            step = "1"
        else:
            start = self.to_c_expr(args[0])
            end = self.to_c_expr(args[1])
            step = self.to_c_expr(args[2])
        self.scope.declare(var, "int")
        self.emit(f"for (int {var} = {start}; {var} < {end}; {var} += {step}) {{")
        self.indent_level += 1
        for stmt in node.body:
            self.visit(stmt)
        self.indent_level -= 1
        self.emit("}")

    def visit_Break(self, node: ast.Break) -> None:
        self.emit("break;")

    def visit_Continue(self, node: ast.Continue) -> None:
        self.emit("continue;")

    def visit_Pass(self, node: ast.Pass) -> None:
        pass

    def _cast_return_expr(self, expr: ast.expr) -> str:
        code = self.to_c_expr(expr)
        ret_type = self.current_return_type or ""
        if not ret_type.endswith("*"):
            return code
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
            cls = expr.func.id
            if cls in DECL_NODE_SUBCLASSES and ret_type == "DeclNode*":
                return f"(DeclNode*)({code})"
        return code

    def visit_Return(self, node: ast.Return) -> None:
        if node.value and isinstance(node.value, ast.Tuple):
            ret_type = self.current_return_type or "Tuple_0"
            field_types = self.tuple_field_types.get(ret_type, [])
            parts: List[str] = []
            for i, elt in enumerate(node.value.elts):
                expr = self.to_c_expr(elt)
                ft = field_types[i] if i < len(field_types) else ""
                vt = self.expr_c_type(elt)
                if ft.endswith("*") and vt and ft != vt:
                    expr = f"({ft})({expr})"
                parts.append(expr)
            self.emit(f"return ({ret_type}){{{', '.join(parts)}}};")
        elif node.value:
            ret_expr = self._cast_return_expr(node.value)
            ret_type = self.current_return_type or ""
            val_type = self.expr_c_type(node.value) if node.value else ""
            if ret_type.endswith("*") and val_type and ret_type != val_type:
                ret_expr = f"({ret_type})({ret_expr})"
            self.emit(f"return {ret_expr};")
        else:
            self.emit("return;")

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            if isinstance(target, ast.Subscript):
                base = self.list_base(target.value)
                if isinstance(target.slice, ast.UnaryOp) and isinstance(target.slice.op, ast.USub):
                    if isinstance(target.slice.operand, ast.Constant):
                        if base != "Unknown":
                            b = base
                            outer = self.to_c_expr(target.value)
                            self.emit(f"{self.list_op(b, 'pop_back', outer)};")
                            continue
                idx = self.to_c_expr(target.slice)
                if base != "Unknown":
                    self.emit(f"{self.list_op(base, 'remove_at', self.to_c_expr(target.value), idx)};")

    def zero_value(self, c_type: str) -> str:
        if c_type == "bool":
            return "false"
        if c_type.endswith("*"):
            return "NULL"
        if c_type.startswith("Tuple_"):
            fields = self.tuple_field_types.get(c_type, [])
            inner = ", ".join(self.zero_value(t) for t in fields)
            return f"({c_type}){{{inner}}}"
        return "0"

    def visit_Raise(self, node: ast.Raise) -> None:
        if node.exc and isinstance(node.exc, ast.Call):
            if isinstance(node.exc.func, ast.Name) and node.exc.func.id == "CompilerError":
                err = self.format_constructor_call("CompilerError", node.exc)
            else:
                err = self.format_constructor_call_from_call(node.exc)
            ret = self.zero_value(self.current_return_type or "void")
            if ret == "":
                self.emit(f"SHIVYCX_RAISE({err});")
            else:
                self.emit(f"do {{ shivycx_pending_error = {err}; return {ret}; }} while(0);")
        else:
            ret = self.zero_value(self.current_return_type or "void")
            self.emit(f"do {{ shivycx_pending_error = NULL; return {ret}; }} while(0);")

    def visit_Try(self, node: ast.Try) -> None:
        self.emit("{")
        self.indent_level += 1
        for stmt in node.body:
            self.visit(stmt)
        self.indent_level -= 1
        for handler in node.handlers:
            exc = handler.type.id if handler.type and isinstance(handler.type, ast.Name) else "Exception"
            self.emit(f"}} /* catch {exc} */ {{")
            self.indent_level += 1
            for stmt in handler.body:
                self.visit(stmt)
            self.indent_level -= 1
        self.emit("}")

    def visit_Expr(self, node: ast.Expr) -> None:
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
            if node.value.func.id == "set_pending_compiler_error":
                args = [self.to_c_expr(a) for a in node.value.args]
                descrip = args[0] if args else '""'
                err_range = args[1] if len(args) > 1 else "NULL"
                self.emit(f"shivycx_pending_error = CompilerError_new({descrip}, {err_range});")
                return
        self.emit(f"{self.to_c_expr(node.value)};")

    def _expr_c_type(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return self.scope.get_type(node.id) or self.global_types.get(node.id) or ""
        if isinstance(node, ast.Attribute):
            if node.attr == "c":
                return "const char*"
            if node.attr == "text_repr":
                return "const char*"
            if node.attr == "content":
                return "const char*"
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return "const char*"
        return ""

    def call_return_type(self, node: ast.Call) -> Optional[str]:
        if isinstance(node.func, ast.Name):
            return self.function_returns.get(node.func.id)
        return None

    def tuple_field_name(self, tpl_type: str, index: int) -> str:
        names = self.tuple_field_names.get(tpl_type)
        if names and index < len(names):
            return names[index]
        return f"f{index}"

    def tuple_subscript(self, base_expr: str, tpl_type: str, index: int) -> str:
        return f"{base_expr}.{self.tuple_field_name(tpl_type, index)}"

    def tuple_type_from_expr(self, node: ast.expr) -> Optional[str]:
        if isinstance(node, ast.Call):
            return self.call_return_type(node)
        if isinstance(node, ast.Name):
            c_type = self.scope.get_type(node.id) or self.global_types.get(node.id) or ""
            if c_type in self.tuple_structs or c_type.startswith("Tuple_"):
                return c_type
        return None

    def infer_type_from_value(self, node: ast.expr) -> str:
        if isinstance(node, ast.Call):
            ret = self.call_return_type(node)
            if ret:
                return ret
            if isinstance(node.func, ast.Name) and node.func.id in self.class_fields:
                return f"{node.func.id}*"
        if isinstance(node, (ast.Attribute, ast.Subscript)):
            t = self.expr_c_type(node)
            if t:
                return t
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return "bool"
            if isinstance(node.value, int):
                return "int"
            if isinstance(node.value, str):
                return "const char*"
        if isinstance(node, ast.Name):
            return self.scope.get_type(node.id) or "int"
        return "int"

    def struct_name_from_type(self, c_type: str) -> Optional[str]:
        if c_type.endswith("*"):
            return c_type[:-1]
        return None

    def lookup_field_type(self, struct: str, attr: str) -> str:
        if self.current_class == struct:
            for c_type, name in self.class_fields.get(struct, []):
                if name == attr:
                    return c_type
        return STRUCT_FIELD_TYPES.get(struct, {}).get(attr, "int")

    def expr_c_type(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return self.scope.get_type(node.id) or self.global_types.get(node.id) or ""
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "parser_utils":
                return self.global_types.get(node.attr, "")
            base_type = self.expr_c_type(node.value)
            struct = self.struct_name_from_type(base_type)
            if struct:
                return self.lookup_field_type(struct, node.attr)
        if isinstance(node, ast.Subscript):
            base = self.list_base(node.value)
            if base != "Unknown":
                return f"{base}*"
            tpl_type = self.tuple_type_from_expr(node.value)
            if tpl_type and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
                fields = self.tuple_field_types.get(tpl_type, [])
                idx = node.slice.value
                if idx < len(fields):
                    return fields[idx]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            cls = node.func.id
            if cls in self.class_fields or cls in KNOWN_CLASSES:
                return f"{cls}*"
        return ""

    def is_pointer_expr(self, node: ast.expr) -> bool:
        c_type = self.expr_c_type(node)
        if c_type:
            return c_type.endswith("*")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            return node.value.id == "self"
        return False

    def to_c_assign_target(self, node: ast.expr) -> str:
        if isinstance(node, ast.Subscript):
            return self.subscript_get(node)
        if isinstance(node, ast.Attribute):
            obj = self.to_c_expr(node.value)
            op = "->" if self.is_pointer_expr(node.value) else "."
            return f"{obj}{op}{node.attr}"
        return self.to_c_expr(node)

    def list_base(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            c_type = self.scope.get_type(node.id) or ""
            if c_type.endswith("*"):
                return c_type[:-1]
        if isinstance(node, ast.Attribute):
            return self.list_base_from_attr(node)
        if isinstance(node, ast.Subscript):
            return self.list_base(node.value)
        return "Unknown"

    def list_base_from_attr(self, node: ast.Attribute) -> str:
        if (
            isinstance(node.value, ast.Name)
            and node.value.id == "parser_utils"
            and node.attr == "tokens"
        ):
            return "Token"
        if isinstance(node.value, ast.Name) and node.value.id == "self" and self.current_class:
            for c_type, attr_name in self.class_fields.get(self.current_class, []):
                if attr_name == node.attr and c_type.endswith("List*"):
                    return c_type[:-1]
        if isinstance(node.value, ast.Name):
            base_type = self.expr_c_type(node.value) or ""
            struct = self.struct_name_from_type(base_type)
            if struct:
                field_type = self.lookup_field_type(struct, node.attr)
                if field_type.endswith("List*"):
                    return field_type.replace("List*", "")
        return "Unknown"

    def expr_points_to_map(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Subscript):
            return self.list_base(node.value) == "StrBoolMapList"
        if isinstance(node, ast.Name):
            c_type = self.scope.get_type(node.id) or ""
            return c_type == "StrBoolMap*"
        return False

    def list_op(self, base: str, op: str, *args: str) -> str:
        if base.endswith("List"):
            fn = f"{base}_{op}"
        else:
            fn = f"{base}List_{op}"
        return f"{fn}({', '.join(args)})"

    def list_len(self, node: ast.expr) -> str:
        if isinstance(node, ast.Attribute):
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "parser_utils"
                and node.attr == "tokens"
            ):
                return "(int)TokenList_len(tokens)"
            if isinstance(node.value, ast.Name) and node.value.id in self.imported_modules:
                attr = node.attr
                if attr in ("symbol_kinds", "keyword_kinds"):
                    return f"(int)TokenKindList_len({attr})"
            base = self.list_base_from_attr(node)
            if base != "Unknown":
                return f"(int){self.list_op(base, 'len', self.to_c_expr(node))}"
        if isinstance(node, ast.Name):
            c_type = self.scope.get_type(node.id) or ""
            if c_type == "const char*":
                return f"(int)strlen({node.id})"
            base = self.list_base(node)
            if base != "Unknown":
                return f"(int){self.list_op(base, 'len', node.id)}"
        return "0"

    def subscript_index(self, node: ast.expr, base: str, outer: str) -> str:
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            if isinstance(node.operand, ast.Constant):
                neg = node.operand.value
                return f"({self.list_op(base, 'len', outer)} - {neg})"
        if isinstance(node, ast.Constant):
            return str(node.value)
        if isinstance(node, ast.Name):
            return node.id
        return self.to_c_expr(node)

    def list_get(self, node: ast.expr, index: str) -> str:
        if isinstance(node, ast.Attribute):
            base = self.list_base_from_attr(node)
            if base != "Unknown":
                return self.list_op(base, "get", self.to_c_expr(node), index)
        if isinstance(node, ast.Name):
            base = self.list_base(node)
            if base != "Unknown":
                return self.list_op(base, "get", node.id, index)
        return f"{self.to_c_expr(node)}[{index}]"
    def subscript_get(self, node: ast.Subscript) -> str:
        if isinstance(node.value, ast.Attribute):
            if (
                isinstance(node.value.value, ast.Name)
                and node.value.value.id == "parser_utils"
                and node.value.attr == "tokens"
            ):
                idx = self.subscript_index(node.slice, "Token", "tokens")
                return f"TokenList_get(tokens, {idx})"
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
            idx = node.slice.value
            tpl_type = self.tuple_type_from_expr(node.value)
            if tpl_type:
                return self.tuple_subscript(self.to_c_expr(node.value), tpl_type, idx)
        if self.expr_points_to_map(node.value):
            container = (
                self.subscript_get(node.value)
                if isinstance(node.value, ast.Subscript)
                else self.to_c_expr(node.value)
            )
            return f"StrBoolMap_get({container}, {self.to_c_expr(node.slice)}, false)"
        if isinstance(node.value, ast.Attribute):
            base = self.list_base_from_attr(node.value)
            if base != "Unknown":
                outer = self.to_c_expr(node.value)
                idx = self.subscript_index(node.slice, base, outer)
                return self.list_op(base, "get", outer, idx)
        if isinstance(node.value, ast.Name):
            val_type = self.scope.get_type(node.value.id) or ""
            if val_type == "const char*":
                if isinstance(node.slice, ast.Constant):
                    return f"{node.value.id}[{node.slice.value}]"
                if isinstance(node.slice, ast.UnaryOp) and isinstance(node.slice.op, ast.USub):
                    return f"{node.value.id}[{self.to_c_expr(node.slice)}]"
                return f"{node.value.id}[{self.to_c_expr(node.slice)}]"
        if isinstance(node.slice, ast.Slice):
            start = self.to_c_expr(node.slice.lower) if node.slice.lower else "0"
            end = self.to_c_expr(node.slice.upper) if node.slice.upper else f"{self.list_len(node.value)}"
            base = self.list_base(node.value)
            if base != "Unknown":
                return self.list_op(base, "slice", self.to_c_expr(node.value), start, end)
            if isinstance(node.value, ast.Name):
                val_type = self.scope.get_type(node.value.id) or ""
                if val_type == "const char*":
                    return f"str_slice({node.value.id}, {start}, {end})"
        if isinstance(node.slice, ast.BinOp):
            base = self.list_base(node.value)
            if base != "Unknown":
                return self.list_op(base, "get", self.to_c_expr(node.value), self.to_c_expr(node.slice))
        if isinstance(node.slice, ast.UnaryOp) and isinstance(node.slice.op, ast.USub):
            if isinstance(node.slice.operand, ast.Constant):
                neg = node.slice.operand.value
                base = self.list_base(node.value)
                if base != "Unknown":
                    return self.list_op(
                        base, "get", self.to_c_expr(node.value),
                        f"({self.list_op(base, 'len', self.to_c_expr(node.value))} - {neg})",
                    )
        if isinstance(node.slice, ast.Constant):
            idx = node.slice.value
            base = self.list_base(node.value)
            if base != "Unknown":
                return self.list_op(base, "get", self.to_c_expr(node.value), str(idx))
            return f"{self.to_c_expr(node.value)}[{idx}]"
        if isinstance(node.slice, ast.Name):
            base = self.list_base(node.value)
            if base != "Unknown":
                return self.list_op(base, "get", self.to_c_expr(node.value), node.slice.id)
        return f"{self.to_c_expr(node.value)}[{self.to_c_expr(node.slice)}]"

    def subscript_set(self, node: ast.Subscript, value: str) -> str:
        if self.expr_points_to_map(node.value):
            container = (
                self.subscript_get(node.value)
                if isinstance(node.value, ast.Subscript)
                else self.to_c_expr(node.value)
            )
            return f"StrBoolMap_set({container}, {self.to_c_expr(node.slice)}, {value})"
        if isinstance(node.slice, ast.Constant):
            idx = node.slice.value
            base = self.list_base(node.value)
            if base != "Unknown":
                return self.list_op(base, "set", self.to_c_expr(node.value), str(idx), value)
            return f"{self.to_c_expr(node.value)}[{idx}] = {value}"
        return f"{self.to_c_expr(node.value)}[{self.to_c_expr(node.slice)}] = {value}"

    def c_arg(self, node: ast.expr) -> str:
        expr = self.to_c_expr(node)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            if (self.scope.get_type(node.value.id) or "") == "const char*":
                return f"char_to_str({expr})"
        return expr

    def format_constructor_call(self, cls: str, node: ast.Call) -> str:
        args: List[str] = []
        for arg_node in node.args:
            arg = self.maybe_upcast_to_node(arg_node, self.c_arg(arg_node))
            args.append(arg)
        if cls == "Range" and len(args) == 1:
            return f"Range_new({args[0]}, {args[0]})"
        if cls == "Declaration" and len(args) == 1:
            return f"Declaration_new({args[0]}, NULL)"
        if cls == "String" and len(args) == 1:
            return f"String_new({args[0]}, false)"
        if cls == "Number" and len(args) == 1:
            arg0 = node.args[0]
            arg_type = self.expr_c_type(arg0)
            if arg_type == "int" or (
                isinstance(arg0, ast.Constant) and isinstance(arg0.value, int)
            ):
                return f"Number_new((void *)(long)({args[0]}))"
        if cls == "InitDesignator" and len(args) == 2:
            return f"InitDesignator_new({args[0]}, (void *)({args[1]}))"
        return f"{cls}_new({', '.join(args)})"

    def format_constructor_call_from_call(self, node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            cls = node.func.id
            if cls in self.class_fields or cls in KNOWN_CLASSES:
                return self.format_constructor_call(cls, node)
        return self.to_c_expr(node)

    def expr_is_str(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return True
        if isinstance(node, ast.Name):
            return (self.scope.get_type(node.id) or "") == "const char*"
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self.expr_is_str(node.left) or self.expr_is_str(node.right)
        return False

    def char_literal(self, ch: str) -> str:
        mapping = {"\n": "'\\n'", "\t": "'\\t'", "\r": "'\\r'", "\\": "'\\\\'", "'": "'\\''", '"': "'\"'"}
        return mapping.get(ch, f"'{ch}'")

    def to_c_expr(self, node: ast.AST) -> str:
        if isinstance(node, ast.Dict):
            if len(node.keys) == 0:
                return "StrBoolMap_new()"
            raise TranspileError("non-empty dict literals are not supported")

        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                escaped = (
                    node.value.replace("\\", "\\\\")
                    .replace('"', '\\"')
                    .replace("\n", "\\n")
                    .replace("\t", "\\t")
                    .replace("\r", "\\r")
                )
                return f'"{escaped}"'
            if isinstance(node.value, bool):
                return "true" if node.value else "false"
            if node.value is None:
                return "NULL"
            return str(node.value)

        if isinstance(node, ast.Name):
            return node.id

        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Name):
                base_type = self.scope.get_type(node.value.value.id) or ""
                if base_type == "Node*" and node.value.attr in NODE_FIELD_CASTS:
                    cast = NODE_FIELD_CASTS[node.value.attr]
                    mid_type = self.lookup_field_type(cast, node.value.attr)
                    mid_struct = self.struct_name_from_type(mid_type)
                    if mid_struct:
                        field_type = self.lookup_field_type(mid_struct, node.attr)
                    else:
                        field_type = self.lookup_field_type(cast, node.attr)
                    if field_type == "const char*" or field_type.endswith("*"):
                        return (
                            f"(({cast} *){node.value.value.id})"
                            f"->{node.value.attr}->{node.attr}"
                        )
            if isinstance(node.value, ast.Name):
                base_type = self.scope.get_type(node.value.id) or ""
                if base_type == "Node*" and node.attr in NODE_FIELD_CASTS:
                    cast = NODE_FIELD_CASTS[node.attr]
                    return f"(({cast} *){node.value.id})->{node.attr}"
            if isinstance(node.value, ast.Name) and node.value.id == "parser_utils":
                global_name = self.parser_utils_global(node.attr)
                if global_name:
                    return global_name
            if isinstance(node.value, ast.Name) and node.value.id in self.imported_modules:
                if node.value.id == "errors_core":
                    if node.attr in ("error_collector", "shivycx_pending_error"):
                        return node.attr
                if node.attr in self.global_types:
                    return node.attr
                return node.attr
            obj = self.to_c_expr(node.value)
            if obj == "self":
                return f"self->{node.attr}"
            if isinstance(node.value, (ast.Subscript, ast.Call)):
                return f"{obj}->{node.attr}"
            op = "->" if self.is_pointer_expr(node.value) else "."
            return f"{obj}{op}{node.attr}"

        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Add):
                left = self.to_c_expr(node.left)
                right_node = node.right
                if self.expr_is_str(node.left) or self.expr_is_str(node.right):
                    if isinstance(right_node, ast.Subscript):
                        return f"str_append_char({left}, {self.to_c_expr(right_node)})"
                    if isinstance(node.left, ast.Subscript):
                        return f"str_append_char({self.to_c_expr(node.right)}, {self.to_c_expr(node.left)})"
                    right = self.to_c_expr(node.right)
                    return f"str_concat({left}, {right})"
            op_map = {
                ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
                ast.Mod: "%", ast.LShift: "<<", ast.RShift: ">>",
                ast.BitAnd: "&", ast.BitOr: "|", ast.BitXor: "^",
            }
            return f"({self.to_c_expr(node.left)} {op_map.get(type(node.op), '?')} {self.to_c_expr(node.right)})"

        if isinstance(node, ast.UnaryOp):
            operand = self.to_c_expr(node.operand)
            if isinstance(node.op, ast.Not):
                if (
                    isinstance(node.operand, ast.Subscript)
                    and isinstance(node.operand.slice, ast.Slice)
                    and node.operand.slice.upper is None
                    and node.operand.slice.step is None
                    and node.operand.slice.lower is not None
                ):
                    start = self.to_c_expr(node.operand.slice.lower)
                    return f"({self.list_len(node.operand.value)} <= {start})"
                return f"(!{operand})"
            if isinstance(node.op, ast.USub):
                return f"(-{operand})"
            return operand

        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.Or) and len(node.values) == 2:
                left_type = self.expr_c_type(node.values[0])
                if left_type == "const char*" or (
                    left_type.endswith("*") and left_type not in ("void*", "bool*")
                ):
                    left = self.to_c_expr(node.values[0])
                    right = self.to_c_expr(node.values[1])
                    return f"({left} ? {left} : {right})"
            op = " && " if isinstance(node.op, ast.And) else " || "
            return "(" + op.join(self.to_c_expr(v) for v in node.values) + ")"

        if isinstance(node, ast.Compare):
            left = self.to_c_expr(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                right = self.to_c_expr(comparator)
                if isinstance(op, ast.NotIn):
                    if isinstance(comparator, ast.Set):
                        checks = []
                        for elt in comparator.elts:
                            checks.append(f"({left} == {self.to_c_expr(elt)})")
                        if checks:
                            return f"(!({' || '.join(checks)}))"
                    if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                        left_expr = left
                        if isinstance(node.left, ast.Attribute) and node.left.attr == "c":
                            left_expr = f"{left_expr}[0]"
                        return f"(!str_contains_char({right}, {left_expr}))"
                if isinstance(op, ast.In):
                    if isinstance(comparator, ast.Name):
                        c_type = self.scope.get_type(comparator.id) or ""
                        if c_type == "StrBoolMap*":
                            return f"StrBoolMap_contains({comparator.id}, {left})"
                    if isinstance(comparator, ast.Set):
                        checks = []
                        for elt in comparator.elts:
                            checks.append(f"({left} == {self.to_c_expr(elt)})")
                        if checks:
                            return f"({' || '.join(checks)})"
                    if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                        if len(comparator.value) == 1:
                            return f"str_contains_char({right}, {left})"
                        left_expr = self.to_c_expr(node.left)
                        if isinstance(node.left, ast.Attribute) and node.left.attr == "c":
                            left_expr = f"{left_expr}[0]"
                        elif left_expr.endswith(".c") or "->c" in left_expr:
                            left_expr = f"{left_expr}[0]"
                        return f"str_contains_char({right}, {left_expr})"
                    if isinstance(left, ast.Constant) and isinstance(left.value, str):
                        return f"(strstr({right}, {left}) != NULL)"
                    return f"str_contains_char({right}, {left})"
                if isinstance(op, ast.Eq):
                    if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                        if len(comparator.value) == 1:
                            lit = comparator.value
                            char_lit = self.char_literal(lit)
                            if isinstance(node.left, ast.Subscript):
                                return f"({self.to_c_expr(node.left)} == {char_lit})"
                            if isinstance(node.left, ast.Subscript) and isinstance(comparator, ast.Name):
                                return f"({self.to_c_expr(node.left)} == {comparator.id}[0])"
                            if isinstance(node.left, ast.Name):
                                t = self.scope.get_type(node.left.id) or self.global_types.get(node.left.id) or ""
                                if t == "const char*":
                                    return f"({node.left.id}[0] == {char_lit})"
                            if isinstance(node.left, ast.Name) and isinstance(node.left.id, str):
                                t = self.scope.get_type(node.left.id) or self.global_types.get(node.left.id) or ""
                                if t == "const char*" and isinstance(comparator, ast.Name):
                                    return f"({left}[0] == {right}[0])"
                        if isinstance(node.left, ast.Name):
                            t = self.scope.get_type(node.left.id) or self.global_types.get(node.left.id) or ""
                            if t == "const char*":
                                return f"(strcmp({node.left.id}, {right}) == 0)"
                        if isinstance(node.left, ast.Attribute):
                            left_attr = self.to_c_expr(node.left)
                            if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                                return f"(strcmp({left_attr}, {right}) == 0)"
                    left_t = self._expr_c_type(node.left)
                    right_t = self._expr_c_type(comparator)
                    if left_t == "const char*" or right_t == "const char*":
                        return f"(strcmp({left}, {right}) == 0)"
                    if isinstance(node.left, ast.Attribute) and node.left.attr == "content":
                        return f"(strcmp({left}, {right}) == 0)"
                    return f"({left} == {right})"
                if isinstance(op, ast.NotEq):
                    left_t = self._expr_c_type(node.left)
                    right_t = self._expr_c_type(comparator)
                    if left_t == "const char*" or right_t == "const char*":
                        return f"(strcmp({left}, {right}) != 0)"
                    if isinstance(node.left, ast.Attribute) and node.left.attr == "content":
                        return f"(strcmp({left}, {right}) != 0)"
                    return f"({left} != {right})"
                if isinstance(op, ast.Lt):
                    return f"({left} < {right})"
                if isinstance(op, ast.LtE):
                    return f"({left} <= {right})"
                if isinstance(op, ast.Gt):
                    return f"({left} > {right})"
                if isinstance(op, ast.GtE):
                    return f"({left} >= {right})"
                if isinstance(op, ast.IsNot):
                    if isinstance(comparator, ast.Constant) and comparator.value is None:
                        return f"({left} != NULL)"
                    return f"({left} != {right})"
                if isinstance(op, ast.Is):
                    if isinstance(comparator, ast.Constant) and comparator.value is None:
                        return f"({left} == NULL)"
                    return f"({left} == {right})"
                left = right
            return left

        if isinstance(node, ast.IfExp):
            return f"({self.to_c_expr(node.test)} ? {self.to_c_expr(node.body)} : {self.to_c_expr(node.orelse)})"

        if isinstance(node, ast.Subscript):
            return self.subscript_get(node)

        if isinstance(node, ast.Call):
            return self.translate_call(node)

        return f"/* Unknown: {ast.dump(node)} */"

    def char_expr(self, node: ast.expr) -> str:
        if isinstance(node, ast.Subscript):
            return self.to_c_expr(node)
        return f"{self.to_c_expr(node)}[0]"

    def maybe_upcast_to_node(self, arg_node: ast.expr, arg: str) -> str:
        if (
            isinstance(arg_node, ast.Call)
            and isinstance(arg_node.func, ast.Name)
            and arg_node.func.id in NODE_UPCAST_CLASSES
        ):
            return f"(Node *)({arg})"
        arg_type = self.expr_c_type(arg_node)
        if arg_type.endswith("*"):
            base = arg_type[:-1]
            if base in NODE_UPCAST_CLASSES and base != "Node":
                return f"(Node *)({arg})"
        return arg

    def format_call_args(self, node: ast.Call) -> List[str]:
        args: List[str] = []
        for arg_node in node.args:
            arg = self.maybe_upcast_to_node(arg_node, self.to_c_expr(arg_node))
            args.append(arg)
        return args

    def translate_call(self, node: ast.Call) -> str:
        args = self.format_call_args(node)
        if isinstance(node.func, ast.Attribute):
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id in self.imported_modules
            ):
                return f"{node.func.attr}({', '.join(args)})"
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "errors_core":
                if node.func.attr == "clear_pending_error":
                    return "clear_pending_error()"
                if node.func.attr == "take_pending_error":
                    return "take_pending_error()"
            if node.func.attr == "add" and isinstance(node.func.value, ast.Attribute):
                if (
                    isinstance(node.func.value.value, ast.Name)
                    and node.func.value.value.id == "errors_core"
                    and node.func.value.attr == "error_collector"
                ):
                    return f"ErrorCollector_add(error_collector, {self.to_c_expr(node.args[0])})"
            if node.func.attr == "add" and isinstance(node.func.value, ast.Name):
                if node.func.value.id == "error_collector":
                    return f"ErrorCollector_add(error_collector, {self.to_c_expr(node.args[0])})"
            if node.func.attr == "append":
                if isinstance(node.func.value, ast.Name):
                    list_name = node.func.value.id
                    base = self.list_base(node.func.value)
                elif isinstance(node.func.value, ast.Attribute):
                    list_name = self.to_c_expr(node.func.value)
                    base = self.list_base_from_attr(node.func.value)
                else:
                    list_name = ""
                    base = "Unknown"
                if base != "Unknown":
                    val = self.to_c_expr(node.args[0])
                    if base == "IntList":
                        return f"IntList_push({list_name}, {val})"
                    if base in ("Node", "NodeList"):
                        val = self.maybe_upcast_to_node(node.args[0], val)
                        return f"NodeList_push({list_name}, {val})"
                    return self.list_op(base, "push", list_name, val)
            if node.func.attr == "extend" and isinstance(node.func.value, ast.Name):
                list_name = node.func.value.id
                base = self.list_base(node.func.value)
                if base != "Unknown":
                    return self.list_op(base, "extend", list_name, self.to_c_expr(node.args[0]))
            if node.func.attr == "pop":
                if isinstance(node.func.value, ast.Name):
                    list_name = node.func.value.id
                    base = self.list_base(node.func.value)
                elif isinstance(node.func.value, ast.Attribute):
                    list_name = self.to_c_expr(node.func.value)
                    base = self.list_base_from_attr(node.func.value)
                else:
                    list_name = ""
                    base = "Unknown"
                if base != "Unknown":
                    return f"{self.list_op(base, 'pop_back', list_name)}"
            if node.func.attr == "isspace":
                obj = self.to_c_expr(node.func.value)
                return f"isspace((unsigned char)({obj}[0]))"
            if node.func.attr in ("isdigit", "isalpha", "isalnum"):
                if isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "c":
                    base = self.to_c_expr(node.func.value.value)
                    ptr = self.to_c_expr(node.func.value)
                    return f"{node.func.attr}((unsigned char)({ptr}[0]))"
                obj = self.char_expr(node.func.value)
                return f"{node.func.attr}((unsigned char)({obj}))"
            if node.func.attr == "lower":
                obj = self.to_c_expr(node.func.value)
                return f"c_tolower_char({obj})"
            if node.func.attr == "rstrip":
                obj = self.to_c_expr(node.func.value)
                chars = self.to_c_expr(node.args[0])
                return f"str_rstrip({obj}, {chars})"
            if node.func.attr == "startswith":
                return f"c_str_startswith({self.to_c_expr(node.func.value)}, {self.to_c_expr(node.args[0])})"
            if node.func.attr == "fullmatch":
                return f"{self.to_c_expr(node.func.value)}_fullmatch({self.to_c_expr(node.args[0])})"
            if node.func.attr == "splitlines":
                return f"str_splitlines({self.to_c_expr(node.func.value)})"
            obj_name = self.to_c_expr(node.func.value)
            cls = self.class_from_expr(node.func.value) or self.current_class or "Unknown"
            args = [obj_name] + [self.to_c_expr(a) for a in node.args]
            return f"{cls}_{node.func.attr}({', '.join(args)})"

        if isinstance(node.func, ast.Name):
            if node.func.id == "match_token":
                args = [self.to_c_expr(a) for a in node.args]
                if len(args) < 4:
                    args.append("NULL")
                return f"match_token({', '.join(args)})"
            if node.func.id == "position_add_col":
                return f"position_add_col({self.to_c_expr(node.args[0])}, {self.to_c_expr(node.args[1])})"
            if node.func.id in self.class_fields or node.func.id in KNOWN_CLASSES:
                if node.func.id == "TokenKind" and len(node.args) == 0:
                    return "TokenKind_new(\"\")"
                return self.format_constructor_call(node.func.id, node)
            if node.func.id == "str_contains_char":
                return f"str_contains_char({self.to_c_expr(node.args[0])}, {self.char_expr(node.args[1])})"
            if node.func.id == "len":
                arg = node.args[0]
                if isinstance(arg, ast.Attribute) and arg.attr == "text_repr":
                    return f"(int)strlen({self.to_c_expr(arg.value)}->text_repr)"
                if isinstance(arg, ast.Attribute) and arg.attr == "c":
                    return f"(int)strlen({self.to_c_expr(arg.value)}->c)"
                if isinstance(arg, ast.Name):
                    t = self.scope.get_type(arg.id) or self.global_types.get(arg.id) or ""
                    if t == "const char*":
                        return f"(int)strlen({arg.id})"
                return self.list_len(arg)
            if node.func.id == "bool":
                return f"(bool)({self.to_c_expr(node.args[0])})"
            if node.func.id == "int" and len(node.args) == 2:
                return f"str_to_int_base({self.to_c_expr(node.args[0])}, {self.to_c_expr(node.args[1])})"
            if node.func.id == "int":
                return f"(int)({self.to_c_expr(node.args[0])})"
            if node.func.id == "dict" and len(node.args) == 1:
                return f"StrBoolMap_copy({self.to_c_expr(node.args[0])})"
            if node.func.id == "ord":
                arg = node.args[0]
                if isinstance(arg, ast.Subscript):
                    val_type = self.expr_c_type(arg.value)
                    if val_type == "const char*":
                        return f"(int)({self.to_c_expr(arg)})"
                if isinstance(arg, ast.Subscript) and isinstance(arg.value, ast.Name):
                    val_type = self.scope.get_type(arg.value.id) or self.global_types.get(arg.value.id) or ""
                    if val_type == "const char*":
                        return f"(int)({self.to_c_expr(arg)})"
                return f"(int)({self.to_c_expr(arg)}[0])"
            if node.func.id == "chr":
                return f"char_to_str((char)({self.to_c_expr(node.args[0])}))"

        return f"{self.to_c_expr(node.func)}({', '.join(args)})"

    def get_output(self) -> str:
        header: List[str] = []
        if self.imports:
            header.append("/* Python imports (wire up in C build): */")
            for imp in self.imports:
                header.append(f"/* {imp} */")
            header.append("")
        return "\n".join(header + self.c_code)


def transpile_source(source: str, module_name: str = "module") -> str:
    tree = ast.parse(source)
    transpiler = ShivyCXTranspiler(module_name=module_name)
    transpiler.visit(tree)
    return transpiler.get_output()


def transpile_file(path: Path) -> str:
    return transpile_source(path.read_text(encoding="utf-8"), module_name=path.stem)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Transpile ShivyC Python modules to C.")
    parser.add_argument("inputs", nargs="*", help="Python source files")
    parser.add_argument("-o", "--output", help="Output .c file")
    parser.add_argument("--demo", action="store_true", help="Run built-in Token sample")
    args = parser.parse_args(argv)

    if args.demo:
        sample = '''
class Token:
    def __init__(self, kind: int, value: str):
        self.kind: int = kind
        self.value: str = value

    def is_match(self, target_kind: int) -> bool:
        if self.kind == target_kind:
            return True
        return False

def run_lexing_test() -> int:
    t: Token = Token(101, "my_identifier")
    matched: bool = t.is_match(101)
    if matched:
        return 0
    return 1
'''
        output = transpile_source(sample, module_name="demo")
    elif args.inputs:
        output = "\n\n".join(transpile_file(Path(p)) for p in args.inputs)
    else:
        parser.print_help()
        return 1

    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
