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
KNOWN_CLASSES = frozenset({
    "Position", "Range", "Tagged", "Token", "TokenKind",
    "CompilerError", "ErrorCollector",
})
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
        self.tuple_type_cache: Dict[Tuple[str, ...], str] = {}
        self.function_returns: Dict[str, str] = {}
        self.current_return_type: Optional[str] = None
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
                inner_list = self.map_type(node_annotation.slice)
                inner_base = inner_list.replace("List*", "")
                self.list_types.add(f"{inner_base}ListList")
                return f"{inner_base}ListList*"
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
        if isinstance(node, ast.Name):
            c_type = self.scope.get_type(node.id) or self.global_types.get(node.id)
            if c_type:
                return c_type.rstrip("*")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            return node.func.id
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
        if self.module_name != "errors_core":
            self.emit('#include "errors_core.h"')
        if self.module_name in ("lexer_core", "token_kinds"):
            self.emit('#include "tokens.h"')
        if self.module_name == "token_kinds":
            self.emit('#include "token_kinds.h"')
        if self.module_name == "lexer_core":
            self.emit('#include "regex_helpers.h"')
            self.emit('#include "token_kinds.h"')
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

    def prescan_function(self, node: ast.FunctionDef) -> None:
        if node.returns:
            ret = self.map_type(node.returns)
            self.function_returns[node.name] = ret
        for arg in node.args.args:
            if arg.annotation:
                self.map_type(arg.annotation)
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.AnnAssign):
                self.map_type(stmt.annotation)

    def emit_list_typedefs(self) -> None:
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
            elif name == "TokenKind" and self.module_name in ("lexer_core", "token_kinds"):
                continue
            elif name == "Str":
                if self.module_name == "lexer_core":
                    continue
                self.emit("typedef struct { char **data; size_t size; size_t capacity; } StrList;")
                self.emit("static inline void StrList_init(StrList *list) { list->data = NULL; list->size = 0; list->capacity = 0; }")
                self.emit("static inline void StrList_push(StrList *list, const char *item) {")
                self.emit("    if (list->size + 1 > list->capacity) {")
                self.emit("        size_t cap = list->capacity ? list->capacity * 2 : 8;")
                self.emit("        list->data = (char **)realloc(list->data, cap * sizeof(char *));")
                self.emit("        list->capacity = cap;")
                self.emit("    }")
                self.emit("    list->data[list->size++] = item;")
                self.emit("}")
                self.emit("static inline size_t StrList_len(const StrList *list) { return list->size; }")
                self.emit("static inline const char *StrList_get(const StrList *list, size_t index) { return list->data[index]; }")
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
            fields_list = self.tuple_field_types.get(name, [])
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
                    "dquote", "squote", "pound", "identifier", "string",
                    "char_string", "include_file", "number", "unrecognized",
                ):
                    self.global_types[kind] = "TokenKind*"
            if alias.name.endswith("errors_core") or base == "errors_core":
                self.imported_modules.add(base)
                self.global_types["error_collector"] = "ErrorCollector*"
                self.global_types["shivycx_pending_error"] = "CompilerError*"

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
                "dquote", "squote", "pound", "identifier", "string",
                "char_string", "include_file", "number", "unrecognized",
            ):
                self.global_types[kind] = "TokenKind*"

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
        if init_method:
            self.generate_constructor(class_name, init_method)

        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name != "__init__":
                self.visit(item)

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
        self.at_module_level = False
        func_name = node.name
        if self.current_class:
            func_name = f"{self.current_class}_{func_name}"

        params = self.format_params(node)
        return_type = self.map_type(node.returns) if node.returns else "void"
        self.current_return_type = return_type

        self.emit(f"{return_type} {func_name}({', '.join(params)}) {{")
        self.indent_level += 1

        for arg in node.args.args:
            if arg.arg == "self" and self.current_class:
                self.scope.declare("self", f"{self.current_class}*")
            elif arg.arg != "self" and arg.annotation:
                self.scope.declare(arg.arg, self.map_type(arg.annotation))

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
                    self.emit(f"{base}_clear({var_name});")
                else:
                    self.emit(f"{var_name} = {self.to_c_expr(node.value)};")

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
                    self.emit(f"{var_name} = {val_str};")
            elif isinstance(target, ast.Attribute):
                obj = self.to_c_expr(target.value)
                op = "->" if self.is_pointer_expr(target.value) else "."
                self.emit(f"{obj}{op}{target.attr} = {val_str};")
            elif isinstance(target, ast.Subscript):
                base = self.list_base(target.value)
                if base != "Unknown":
                    self.emit(f"{self.list_op(base, 'set', self.to_c_expr(target.value), self.to_c_expr(target.slice), val_str)};")
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

    def visit_For(self, node: ast.For) -> None:
        if isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Name):
            if node.iter.func.id == "range":
                self.emit_range_for(node)
                return
            if node.iter.func.id == "enumerate":
                self.emit_enumerate_for(node)
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
        return "void*"

    def emit_tuple_unpack(self, target: ast.Tuple, value: ast.expr) -> None:
        tmp = "_unpack_tmp"
        val = self.to_c_expr(value)
        tpl_type = "Tuple_0"
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            tpl_type = self.function_returns.get(value.func.id, tpl_type)
        fields = self.tuple_field_types.get(tpl_type, [])
        self.emit(f"{tpl_type} {tmp} = {val};")
        for i, elt in enumerate(target.elts):
            if isinstance(elt, ast.Name):
                c_type = fields[i] if i < len(fields) else "void*"
                if elt.id not in self.scope.declared:
                    self.scope.declare(elt.id, c_type)
                    self.emit(f"{c_type} {elt.id} = {tmp}.f{i};")
                else:
                    self.emit(f"{elt.id} = {tmp}.f{i};")

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

    def visit_Return(self, node: ast.Return) -> None:
        if node.value and isinstance(node.value, ast.Tuple):
            ret_type = self.current_return_type or "Tuple_0"
            fields = ", ".join(self.to_c_expr(e) for e in node.value.elts)
            self.emit(f"return ({ret_type}){{{fields}}};")
        elif node.value:
            self.emit(f"return {self.to_c_expr(node.value)};")
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

    def infer_type_from_value(self, node: ast.expr) -> str:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in self.class_fields:
                return f"{node.func.id}*"
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

    def is_pointer_expr(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            c_type = self.scope.get_type(node.id) or self.global_types.get(node.id) or ""
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
        if isinstance(node, ast.Subscript):
            return self.list_base(node.value)
        return "Unknown"

    def list_op(self, base: str, op: str, *args: str) -> str:
        if base.endswith("List"):
            fn = f"{base}_{op}"
        else:
            fn = f"{base}List_{op}"
        return f"{fn}({', '.join(args)})"

    def list_len(self, node: ast.expr) -> str:
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in self.imported_modules:
                attr = node.attr
                if attr in ("symbol_kinds", "keyword_kinds"):
                    return f"(int)TokenKindList_len({attr})"
        if isinstance(node, ast.Name):
            c_type = self.scope.get_type(node.id) or ""
            if c_type == "const char*":
                return f"(int)strlen({node.id})"
            base = self.list_base(node)
            if base != "Unknown":
                return f"(int){self.list_op(base, 'len', node.id)}"
        return "0"

    def list_get(self, node: ast.expr, index: str) -> str:
        if isinstance(node, ast.Name):
            base = self.list_base(node)
            if base != "Unknown":
                return self.list_op(base, "get", node.id, index)
        return f"{self.to_c_expr(node)}[{index}]"
    def subscript_get(self, node: ast.Subscript) -> str:
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
        args = [self.c_arg(a) for a in node.args]
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
            if isinstance(node.value, ast.Name) and node.value.id in self.imported_modules:
                if node.value.id == "errors_core":
                    if node.attr in ("error_collector", "shivycx_pending_error"):
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
            }
            return f"({self.to_c_expr(node.left)} {op_map.get(type(node.op), '?')} {self.to_c_expr(node.right)})"

        if isinstance(node, ast.UnaryOp):
            operand = self.to_c_expr(node.operand)
            if isinstance(node.op, ast.Not):
                return f"(!{operand})"
            if isinstance(node.op, ast.USub):
                return f"(-{operand})"
            return operand

        if isinstance(node, ast.BoolOp):
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

    def translate_call(self, node: ast.Call) -> str:
        if isinstance(node.func, ast.Attribute):
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
            if node.func.attr == "append" and isinstance(node.func.value, ast.Name):
                list_name = node.func.value.id
                base = self.list_base(node.func.value)
                if base != "Unknown":
                    if base == "IntList":
                        return f"IntList_push({list_name}, {self.to_c_expr(node.args[0])})"
                    return self.list_op(base, "push", list_name, self.to_c_expr(node.args[0]))
            if node.func.attr == "extend" and isinstance(node.func.value, ast.Name):
                list_name = node.func.value.id
                base = self.list_base(node.func.value)
                if base != "Unknown":
                    return self.list_op(base, "extend", list_name, self.to_c_expr(node.args[0]))
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
            if node.func.id == "ord":
                arg = node.args[0]
                if isinstance(arg, ast.Subscript) and isinstance(arg.value, ast.Name):
                    val_type = self.scope.get_type(arg.value.id) or self.global_types.get(arg.value.id) or ""
                    if val_type == "const char*":
                        return f"(int)({self.to_c_expr(arg)})"
                return f"(int)({self.to_c_expr(arg)}[0])"

        args = [self.to_c_expr(a) for a in node.args]
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
