"""Comparison and equality expression nodes in the AST."""

import shivyc.ctypes as ctypes
import shivyc.il_cmds.compare as compare_cmds
from shivyc.errors import CompilerError
from shivyc.il_gen import ILValue
from shivyc.tree.arithmetic_exprs import _ArithBinOp
from shivyc.tree.utils import set_type, check_cast, report_err


class _Equality(_ArithBinOp):
    """Base class for == and != nodes."""

    eq_il_cmd = None

    def __init__(self, left, right, op):
        """Initialize node."""
        super().__init__(left, right, op)

    def _arith(self, left, right, il_code):
        """Check equality of arithmetic expressions."""
        out = ILValue(ctypes.integer)
        il_code.add(self.eq_il_cmd(out, left, right))
        return out

    # Comparisons always yield int, even when constant-folded.
    def _const_ctype(self, operand_ctype):
        return ctypes.integer

    def _nonarith(self, left, right, il_code):
        """Check equality of non-arithmetic expressions."""

        # Capture object identity before any pointer cast below replaces the
        # operands: `&a == &b` and `&a != &b` are compile-time constants because
        # distinct named objects have distinct addresses (and `&a == &a` is
        # true). This lets the address-comparison build-assert
        # `sizeof(char[1 - 2 * !(&a != &b)])` evaluate at compile time.
        left_obj = left.addr_of
        right_obj = right.addr_of

        # If either operand is a null pointer constant, cast it to the
        # other's pointer type.
        if (left.ctype.is_pointer()
             and getattr(right.literal, "val", None) == 0):
            right = set_type(right, left.ctype, il_code)
        elif (right.ctype.is_pointer()
              and getattr(left.literal, "val", None) == 0):
            left = set_type(left, right.ctype, il_code)

        # If both operands are not pointer types, quit now
        if not left.ctype.is_pointer() or not right.ctype.is_pointer():
            with report_err():
                err = "comparison between incomparable types"
                raise CompilerError(err, self.op.r)

        # If one side is pointer to void, both convert to a void pointer. C11
        # 6.5.9p2 allows comparing a void pointer with any object pointer, so
        # the only thing to preserve is the combined qualification of the
        # pointed-to types -- casting the qualified side down to a bare `void *`
        # (and thus discarding `const`) would wrongly be rejected as an
        # incompatible-pointer conversion.
        elif left.ctype.arg.is_void() or right.ctype.arg.is_void():
            const = left.ctype.arg.const or right.ctype.arg.const
            void_arg = ctypes.void.make_const() if const else ctypes.void
            void_ptr = ctypes.PointerCType(void_arg)
            left = set_type(left, void_ptr, il_code)
            right = set_type(right, void_ptr, il_code)

        # If both types are still incompatible, warn! Qualifier differences on
        # the pointed-to type (e.g. `char *` vs `const char *`) are allowed.
        elif not left.ctype.arg.make_unqual().compatible(
                right.ctype.arg.make_unqual()):
            with report_err():
                err = "comparison between distinct pointer types"
                raise CompilerError(err, self.op.r)

        # Both operands are addresses of whole named objects: fold to a
        # constant from object identity rather than emitting a runtime compare.
        if left_obj is not None and right_obj is not None:
            same = left_obj is right_obj
            out = ILValue(ctypes.integer)
            il_code.register_literal_var(
                out, self._arith_const(0, 0 if same else 1, ctypes.integer))
            return out

        # Now, we can do comparison
        out = ILValue(ctypes.integer)
        il_code.add(self.eq_il_cmd(out, left, right))
        return out


class Equality(_Equality):
    """Expression that checks equality of two expressions."""

    eq_il_cmd = compare_cmds.EqualCmp

    def _arith_const(self, left, right, ctype):
        return 1 if left == right else 0


class Inequality(_Equality):
    """Expression that checks inequality of two expressions."""

    eq_il_cmd = compare_cmds.NotEqualCmp

    def _arith_const(self, left, right, ctype):
        return 1 if left != right else 0


class _Relational(_ArithBinOp):
    """Base class for <, <=, >, and >= nodes."""

    comp_cmd = None

    def __init__(self, left, right, op):
        """Initialize node."""
        super().__init__(left, right, op)

    def _arith(self, left, right, il_code):
        """Compare arithmetic expressions."""
        out = ILValue(ctypes.integer)
        il_code.add(self.comp_cmd(out, left, right))
        return out

    # Comparisons always yield int, even when constant-folded.
    def _const_ctype(self, operand_ctype):
        return ctypes.integer

    def _nonarith(self, left, right, il_code):
        """Compare non-arithmetic expressions."""

        if not left.ctype.is_pointer() or not right.ctype.is_pointer():
            err = "comparison between incomparable types"
            raise CompilerError(err, self.op.r)
        elif not left.ctype.arg.make_unqual().compatible(
                right.ctype.arg.make_unqual()):
            err = "comparison between distinct pointer types"
            raise CompilerError(err, self.op.r)

        out = ILValue(ctypes.integer)
        il_code.add(self.comp_cmd(out, left, right))
        return out


class LessThan(_Relational):
    """Less than comparison expression."""
    comp_cmd = compare_cmds.LessCmp

    def _arith_const(self, left, right, ctype):
        return 1 if left < right else 0


class GreaterThan(_Relational):
    """Greater than comparison expression."""
    comp_cmd = compare_cmds.GreaterCmp

    def _arith_const(self, left, right, ctype):
        return 1 if left > right else 0


class LessThanOrEq(_Relational):
    """Less than or equal comparison expression."""
    comp_cmd = compare_cmds.LessOrEqCmp

    def _arith_const(self, left, right, ctype):
        return 1 if left <= right else 0


class GreaterThanOrEq(_Relational):
    """Greater than or equal comparison expression."""
    comp_cmd = compare_cmds.GreaterOrEqCmp

    def _arith_const(self, left, right, ctype):
        return 1 if left >= right else 0
