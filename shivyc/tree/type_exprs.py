"""Type operation expression nodes in the AST."""

import shivyc.ctypes as ctypes
import shivyc.token_kinds as token_kinds
import shivyc.tree.decl_nodes as decl_nodes
from shivyc.errors import CompilerError
from shivyc.il_gen import ILValue
from shivyc.tree.expr_base import _RExprNode, _LExprNode
from shivyc.tree.general_nodes import Declaration, DeclInfo
from shivyc.tree.utils import set_type, DirectLValue
from shivyc.tokens import Token


class _SizeofNode(_RExprNode):
    """Base class for common logic for the two sizeof nodes."""

    def __init__(self):
        super().__init__()

    def sizeof_ctype(self, ctype, range, il_code):
        """Raise CompilerError if ctype is not valid as sizeof argument."""

        if ctype.is_function():
            err = "sizeof argument cannot have function type"
            raise CompilerError(err, range)

        if ctype.is_incomplete():
            err = "sizeof argument cannot have incomplete type"
            raise CompilerError(err, range)

        # sizeof exposes the struct's size; it must keep all its members.
        if ctype.is_struct_union():
            import shivyc.member_elim as member_elim
            member_elim.mark_ineligible(getattr(ctype, "tag", None))

        out = ILValue(ctypes.unsig_longint)
        il_code.register_literal_var(out, ctype.size)
        return out


class SizeofExpr(_SizeofNode):
    """Node representing sizeof with expression operand.

    expr (_ExprNode) - the expression to get the size of
    """
    def __init__(self, expr):
        super().__init__()
        self.expr = expr

    def make_il(self, il_code: "il_gen.ILCode", symbol_table: "il_gen.SymbolTable", c):
        """Return a compile-time integer literal as the expression size."""

        dummy_il_code = il_code.copy_code()
        expr = self.expr.make_il_raw(dummy_il_code, symbol_table, c)
        return self.sizeof_ctype(expr.ctype, self.expr.r, il_code)


class SizeofType(Declaration, _SizeofNode):
    """Node representing sizeof with abstract type as operand.

    node (decl_nodes.Root) - a declaration tree for the type

    Declaration is listed first so that under the self-host transpiler it is the
    struct-layout (prefix) base: SizeofType reaches Declaration's instance fields
    (node, il_code, ...) through Declaration's own methods (make_specs_ctype,
    set_self_vars), which address those fields at Declaration's offsets. The
    _SizeofNode mixin carries no instance state, so it is layout-safe as a
    secondary base. CPython is order-insensitive here (make_il is overridden).
    """
    def __init__(self, node):
        _SizeofNode.__init__(self)
        Declaration.__init__(self, node)   # sets self.node = node

    def make_il(self, il_code: "il_gen.ILCode", symbol_table: "il_gen.SymbolTable", c):
        """Return a compile-time integer literal as the expression size."""

        self.set_self_vars(il_code, symbol_table, c)
        base_type, _ = self.make_specs_ctype(self.node.specs, True)
        ctype, _ = self.make_ctype(self.node.decls[0], base_type)
        return self.sizeof_ctype(ctype, self.node.decls[0].r, il_code)


class _AlignofNode(_RExprNode):
    """Base class for the two _Alignof nodes (mirrors _SizeofNode)."""

    def __init__(self):
        super().__init__()

    def alignof_ctype(self, ctype, range, il_code):
        if ctype.is_function():
            err = "_Alignof argument cannot have function type"
            raise CompilerError(err, range)
        if ctype.is_incomplete():
            err = "_Alignof argument cannot have incomplete type"
            raise CompilerError(err, range)
        if ctype.is_struct_union():
            import shivyc.member_elim as member_elim
            member_elim.mark_ineligible(getattr(ctype, "tag", None))
        out = ILValue(ctypes.unsig_longint)
        il_code.register_literal_var(out, ctype.alignment())
        return out


class AlignofExpr(_AlignofNode):
    """Node representing _Alignof with an expression operand (GCC-style)."""

    def __init__(self, expr):
        super().__init__()
        self.expr = expr

    def make_il(self, il_code: "il_gen.ILCode", symbol_table: "il_gen.SymbolTable", c):
        dummy_il_code = il_code.copy_code()
        expr = self.expr.make_il_raw(dummy_il_code, symbol_table, c)
        return self.alignof_ctype(expr.ctype, self.expr.r, il_code)


class AlignofType(Declaration, _AlignofNode):
    """Node representing _Alignof with an abstract type-name operand.

    Declaration is listed first for the same struct-layout reason as
    SizeofType (the _AlignofNode mixin carries no instance state).
    """
    def __init__(self, node):
        _AlignofNode.__init__(self)
        Declaration.__init__(self, node)

    def make_il(self, il_code: "il_gen.ILCode", symbol_table: "il_gen.SymbolTable", c):
        self.set_self_vars(il_code, symbol_table, c)
        base_type, _ = self.make_specs_ctype(self.node.specs, True)
        ctype, _ = self.make_ctype(self.node.decls[0], base_type)
        return self.alignof_ctype(ctype, self.node.decls[0].r, il_code)


class OffsetofType(Declaration, _RExprNode):
    """Node representing __builtin_offsetof(type-name, member-designator).

    node (decl_nodes.Root) - declaration tree for the type-name operand
    designator (list) - the member designator as a list of ('member', name)
        and ('index', expr_node) steps, e.g. `a.b[3].c` ->
        [('member','a'),('member','b'),('index',<3>),('member','c')].
    """
    def __init__(self, node, designator):
        Declaration.__init__(self, node)   # sets self.node = node
        _RExprNode.__init__(self)
        self.designator = designator

    def make_il(self, il_code: "il_gen.ILCode", symbol_table: "il_gen.SymbolTable", c):
        """Return a size_t literal equal to the member's byte offset."""
        self.set_self_vars(il_code, symbol_table, c)
        base_type, _ = self.make_specs_ctype(self.node.specs, True)
        ctype, _ = self.make_ctype(self.node.decls[0], base_type)

        offset = 0
        import shivyc.member_elim as member_elim
        for kind, val in self.designator:
            if kind == "member":
                if not ctype.is_struct_union():
                    err = "request for member in non-struct/union type"
                    raise CompilerError(err, self.r)
                # offsetof exposes a member's offset; removing an earlier
                # member would shift it, so this struct keeps all members.
                member_elim.mark_ineligible(getattr(ctype, "tag", None))
                off, mctype = ctype.get_offset(val)
                if off is None:
                    err = f"structure or union has no member '{val}'"
                    raise CompilerError(err, self.r)
                offset += off
                ctype = mctype
            else:  # "index"
                if not ctype.is_array():
                    err = "subscripted value in offsetof is not an array"
                    raise CompilerError(err, self.r)
                idx = val.make_il(il_code.copy_code(), symbol_table, c)
                if not idx.literal:
                    err = "offsetof array index must be a compile-time constant"
                    raise CompilerError(err, self.r)
                offset += idx.literal.val * ctype.el.size
                ctype = ctype.el

        out = ILValue(ctypes.unsig_longint)
        il_code.register_literal_var(out, offset)
        return out


class Cast(Declaration, _RExprNode):
    """Node representing a cast operation, like `(void*)p`.

    node (decl_nodes.Root) - a declaration tree for this line

    TODO: Share code between Cast and Declaration nodes more cleanly.
    """
    def __init__(self, node, expr):
        Declaration.__init__(self, node)   # sets self.node = node
        _RExprNode.__init__(self)

        self.expr = expr

    def make_il(self, il_code: "il_gen.ILCode", symbol_table: "il_gen.SymbolTable", c):
        """Make IL for this cast operation."""

        self.set_self_vars(il_code, symbol_table, c)
        base_type, _ = self.make_specs_ctype(self.node.specs, True)
        ctype, _ = self.make_ctype(self.node.decls[0], base_type)

        if not ctype.is_void() and not ctype.is_scalar():
            err = "can only cast to scalar or void type"
            raise CompilerError(err, self.node.decls[0].r)

        il_value = self.expr.make_il(il_code, symbol_table, c)
        if ctype.is_void():
            # `(void)expr` is the standard discard idiom: evaluate for any
            # side effects and throw the value away. Valid for any source type
            # (e.g. discarding an unused union-typed parameter).
            return il_value
        if not il_value.ctype.is_scalar():
            err = "can only cast from scalar type"
            raise CompilerError(err, self.r)

        return set_type(il_value, ctype, il_code)


class CompoundLiteral(_LExprNode):
    """A C99 compound literal: ( type-name ) { initializer-list }.

    Creates an anonymous object of the given type, initializes it from the
    brace list, and is an lvalue referring to that object. Inside a function
    the object has automatic storage; at file scope it has static storage.
    """

    _counter = 0

    def __init__(self, node, init):
        super().__init__()
        self.node = node          # decl_nodes.Root for the type-name
        self.init = init          # initializer (InitList or expr node)

    def _lvalue(self, il_code, symbol_table, c):
        # Use a Declaration instance purely for its type-resolution helpers
        # (kept separate to avoid inheriting Declaration's make_il).
        helper = Declaration(self.node)
        helper.set_self_vars(il_code, symbol_table, c)

        root = decl_nodes.Root(self.node.specs, self.node.decls, [self.init])
        info: "DeclInfo" = helper.get_decl_infos(root)[0]

        # The object is anonymous; give it a unique synthetic name.
        CompoundLiteral._counter += 1
        name = "__compound.%d" % CompoundLiteral._counter
        ident = Token(token_kinds.identifier, name, r=self.r)
        info.identifier = ident

        storage = (symbol_table.STATIC if c.is_global
                   else symbol_table.AUTOMATIC)
        var = symbol_table.add_variable(
            ident, info.ctype, symbol_table.DEFINED, None, storage)
        if info.init is not None:
            info.do_init(var, storage, il_code, symbol_table, c)
        return DirectLValue(var)
