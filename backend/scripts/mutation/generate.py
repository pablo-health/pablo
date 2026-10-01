# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Generate mutants: each one a single small change to a module's syntax tree.

A mutant records where its node sits in the tree (``path``: a list of
``[field, index]`` steps from the module root) so the runner can rebuild it
from a fresh parse. ``func``, ``op``, ``desc`` and ``snippet`` describe it in
terms that survive the module being edited, which is how a survivor judged in
one run keeps its judgement in the next (see ``score.py``).

Left alone: imports, asserts, ``TYPE_CHECKING`` blocks, ``__all__``,
docstrings and type annotations. String constants inside log calls, error
messages and f-strings are still mutated, but tagged, so a report can set
them aside.

The traversal order and the wording of ``desc`` are part of the contract:
``classified.json`` keys on them.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

Mutant = dict[str, Any]
NodePath = list[list[Any]]

CMP_SWAP: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}
BIN_SWAP: dict[type[ast.operator], type[ast.operator]] = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
    ast.FloorDiv: ast.Div,
}
NAME_SWAP = {"min": "max", "max": "min", "any": "all", "all": "any"}
ATTR_SWAP = {
    "startswith": "endswith",
    "endswith": "startswith",
    "lower": "upper",
    "upper": "lower",
    "strip": "lstrip",
}
SYMBOL: dict[type[ast.AST], str] = {
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.In: "in",
    ast.NotIn: "not in",
    ast.Is: "is",
    ast.IsNot: "is not",
    ast.And: "and",
    ast.Or: "or",
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.FloorDiv: "//",
}
_ERROR_TYPES = frozenset({"ValueError", "RuntimeError", "TypeError"})
_ANNOTATION_FIELDS = frozenset({"annotation", "returns"})
_SIMPLE_STATEMENTS = (ast.Expr, ast.Assign, ast.AugAssign, ast.AnnAssign)
_NEVER_DELETED = (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom, ast.Pass, ast.Assert)


def _is_bare_string(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _assigns(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == name for target in node.targets
    )


def _skipped(node: ast.AST) -> bool:
    if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assert)):
        return True
    if (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "TYPE_CHECKING"
    ):
        return True
    return _assigns(node, "__all__") or _is_bare_string(node)


def _flags(node: ast.AST, inherited: frozenset[str]) -> frozenset[str]:
    """What kind of text a string constant under this node would be."""
    flags = set(inherited)
    if _assigns(node, "msg"):
        flags.add("msg")
    if isinstance(node, ast.Call):
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "logger"
        ):
            flags.add("log")
        if isinstance(func, ast.Name) and func.id in _ERROR_TYPES:
            flags.add("msg")
    if isinstance(node, ast.JoinedStr):
        flags.add("fstr")
    return frozenset(flags)


def _snippet(node: ast.AST) -> str:
    text = ast.unparse(node)
    return text.split("\n")[0][:80] if isinstance(node, ast.stmt) else text[:80]


def _string_tag(flags: frozenset[str]) -> str:
    for tag in ("log", "msg", "fstr"):
        if tag in flags:
            return tag
    return ""


class _Generator:
    def __init__(self, rel: str, source: str) -> None:
        self.rel = rel
        self.tree = ast.parse(source)
        self.mutants: list[Mutant] = []
        self.functions: list[str] = []
        self.classes: list[str] = []
        self.handlers: dict[type[ast.AST], Callable[[Any, NodePath, frozenset[str]], None]] = {
            ast.Compare: self._compare,
            ast.BoolOp: self._boolop,
            ast.UnaryOp: self._unaryop,
            ast.BinOp: self._binop,
            ast.AugAssign: self._augassign,
            ast.Constant: self._constant,
            ast.Return: self._return,
            ast.If: self._condition,
            ast.IfExp: self._condition,
            ast.While: self._condition,
            ast.comprehension: self._comprehension,
            ast.Name: self._name,
            ast.Attribute: self._attribute,
            ast.Call: self._call,
        }

    def run(self) -> list[Mutant]:
        self._visit(self.tree, [], frozenset())
        return self.mutants

    def _add(self, node: ast.AST, op: str, desc: str, path: NodePath, **extra: Any) -> None:
        self.mutants.append(
            {
                "file": self.rel,
                "func": self.functions[-1] if self.functions else "<module>",
                "line": getattr(node, "lineno", 0),
                "op": op,
                "desc": desc,
                "path": path,
                "snippet": _snippet(node),
                **extra,
            }
        )

    # --- walking -----------------------------------------------------------

    def _visit(self, node: ast.AST, path: NodePath, inherited: frozenset[str]) -> None:
        if _skipped(node):
            return
        flags = _flags(node, inherited)
        entered_function = self._enter_function(node, path)
        entered_class = isinstance(node, ast.ClassDef)
        if isinstance(node, ast.ClassDef):
            self.classes.append(node.name)
        if "annotation" not in flags:
            handler = self.handlers.get(type(node))
            if handler is not None:
                handler(node, path, flags)
        self._visit_children(node, path, flags)
        if entered_function:
            self.functions.pop()
        if entered_class:
            self.classes.pop()

    def _enter_function(self, node: ast.AST, path: NodePath) -> bool:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return False
        name = f"{self.classes[-1]}.{node.name}" if self.classes else node.name
        self.functions.append(name)
        first = node.body[0] if node.body else None
        after_docstring = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
        self._add(
            node,
            "early_return",
            f"insert `return None` at top of {name}",
            path,
            insert_index=1 if after_docstring else 0,
        )
        return True

    def _visit_children(self, node: ast.AST, path: NodePath, flags: frozenset[str]) -> None:
        for field, value in ast.iter_fields(node):
            child_flags = flags | {"annotation"} if field in _ANNOTATION_FIELDS else flags
            if isinstance(value, ast.AST):
                self._visit(value, [*path, [field, None]], child_flags)
                continue
            if not isinstance(value, list):
                continue
            for index, item in enumerate(value):
                if not isinstance(item, ast.AST):
                    continue
                child_path = [*path, [field, index]]
                if isinstance(item, ast.stmt) and "annotation" not in flags:
                    self._maybe_delete(node, field, item, child_path)
                self._visit(item, child_path, child_flags)

    def _maybe_delete(self, parent: ast.AST, field: str, stmt: ast.stmt, path: NodePath) -> None:
        if isinstance(stmt, _NEVER_DELETED) or not self.functions:
            return
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            return
        in_if = isinstance(parent, ast.If) and field in {"body", "orelse"}
        if in_if or isinstance(stmt, _SIMPLE_STATEMENTS):
            first = ast.unparse(stmt).split("\n")[0][:70]
            where = "if-body" if in_if else "body"
            self._add(stmt, "delete_stmt", f"delete {where} statement `{first}`", path)

    # --- one handler per node type ------------------------------------------

    def _compare(self, node: ast.Compare, path: NodePath, _flags: frozenset[str]) -> None:
        for index, op in enumerate(node.ops):
            new = CMP_SWAP.get(type(op))
            if new is not None:
                desc = f"`{SYMBOL[type(op)]}` -> `{SYMBOL[new]}`"
                self._add(node, "compare", desc, path, op_index=index, new_op=new.__name__)

    def _boolop(self, node: ast.BoolOp, path: NodePath, _flags: frozenset[str]) -> None:
        new = ast.Or if isinstance(node.op, ast.And) else ast.And
        desc = f"`{SYMBOL[type(node.op)]}` -> `{SYMBOL[new]}`"
        self._add(node, "boolop", desc, path, new_op=new.__name__)

    def _unaryop(self, node: ast.UnaryOp, path: NodePath, _flags: frozenset[str]) -> None:
        if isinstance(node.op, ast.Not):
            self._add(node, "drop_not", "remove `not`", path)

    def _binop(self, node: ast.BinOp, path: NodePath, _flags: frozenset[str]) -> None:
        new = BIN_SWAP.get(type(node.op))
        if new is not None:
            desc = f"`{SYMBOL[type(node.op)]}` -> `{SYMBOL[new]}`"
            self._add(node, "binop", desc, path, new_op=new.__name__)

    def _augassign(self, node: ast.AugAssign, path: NodePath, _flags: frozenset[str]) -> None:
        if isinstance(node.op, (ast.Add, ast.Sub)):
            new = ast.Sub if isinstance(node.op, ast.Add) else ast.Add
            desc = f"`{SYMBOL[type(node.op)]}=` -> `{SYMBOL[new]}=`"
            self._add(node, "augassign", desc, path, new_op=new.__name__)

    def _constant(self, node: ast.Constant, path: NodePath, flags: frozenset[str]) -> None:
        value = node.value
        if isinstance(value, bool):
            self._add(node, "const_bool", f"`{value}` -> `{not value}`", path, new_value=not value)
        elif isinstance(value, int):
            for new in (value + 1, value - 1):
                self._add(node, "const_int", f"`{value}` -> `{new}`", path, new_value=new)
        elif isinstance(value, str):
            new_text = value + "XX"
            desc = f"`{value!r}` -> `{new_text!r}`"
            tag = _string_tag(flags)
            self._add(node, "const_str", desc, path, new_value=new_text, tag=tag)

    def _return(self, node: ast.Return, path: NodePath, _flags: frozenset[str]) -> None:
        value = node.value
        if value is None or (isinstance(value, ast.Constant) and value.value is None):
            return
        desc = f"`return {ast.unparse(value)[:50]}` -> `return None`"
        self._add(node, "return_none", desc, path)

    def _condition(
        self, node: ast.If | ast.IfExp | ast.While, path: NodePath, _flags: frozenset[str]
    ) -> None:
        for value in (True, False):
            desc = f"condition `{ast.unparse(node.test)[:50]}` -> `{value}`"
            self._add(node, "cond_const", desc, path, new_value=value)

    def _comprehension(
        self, node: ast.comprehension, path: NodePath, _flags: frozenset[str]
    ) -> None:
        for index, test in enumerate(node.ifs):
            desc = f"comprehension filter `{ast.unparse(test)[:50]}` -> `True`"
            self._add(node, "comp_if_true", desc, path, if_index=index)

    def _name(self, node: ast.Name, path: NodePath, _flags: frozenset[str]) -> None:
        new = NAME_SWAP.get(node.id)
        if new is not None:
            self._add(node, "name_swap", f"`{node.id}` -> `{new}`", path, new_value=new)

    def _attribute(self, node: ast.Attribute, path: NodePath, _flags: frozenset[str]) -> None:
        new = ATTR_SWAP.get(node.attr)
        if new is not None:
            self._add(node, "attr_swap", f"`.{node.attr}` -> `.{new}`", path, new_value=new)

    def _call(self, node: ast.Call, path: NodePath, _flags: frozenset[str]) -> None:
        for index, keyword in enumerate(node.keywords):
            if keyword.arg is not None and isinstance(keyword.value, ast.Constant):
                desc = f"drop keyword `{keyword.arg}={ast.unparse(keyword.value)}`"
                self._add(node, "drop_kwarg", desc, path, kw_index=index)


def generate_module(rel: str, source: str) -> list[Mutant]:
    """Every mutant of one module, in traversal order, without ids."""
    return _Generator(rel, source).run()


def generate(backend: Path, modules: tuple[str, ...] | list[str]) -> list[Mutant]:
    """Every mutant of the modules, numbered in order."""
    mutants: list[Mutant] = []
    for rel in modules:
        mutants.extend(generate_module(rel, (backend / rel).read_text()))
    for index, mutant in enumerate(mutants):
        mutant["id"] = index
    return mutants
