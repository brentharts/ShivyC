"""Control flow statement nodes in the AST."""

import shivyc.il_cmds.control as control_cmds
from shivyc.errors import CompilerError
from shivyc.tokens import Token
from shivyc.il_gen import ILValue
from shivyc.tree.base_nodes import CNode
from shivyc.tree.utils import report_err, set_type, check_cast


class Return(CNode):
    """CNode for a return statement."""

    def __init__(self, return_value):
        """Initialize node."""
        super().__init__()
        self.return_value = return_value

    def make_il(self, il_code: "il_gen.ILCode", symbol_table: "il_gen.SymbolTable", c):
        """Make IL code for returning this value."""

        if self.return_value and c.sret_ptr is not None:
            # SysV memory-class return: copy the struct into the caller's
            # storage via the hidden pointer, then return that pointer in RAX.
            il_value = self.return_value.make_il(il_code, symbol_table, c)
            check_cast(il_value, c.return_type, self.return_value.r)
            ret = set_type(il_value, c.return_type, il_code)
            from shivyc.tree.utils import IndirectLValue
            IndirectLValue(c.sret_ptr).set_to(
                ret, il_code, self.return_value.r)
            il_code.add(control_cmds.Return(c.sret_ptr))
        elif self.return_value and not c.return_type.is_void():
            il_value = self.return_value.make_il(il_code, symbol_table, c)
            check_cast(il_value, c.return_type, self.return_value.r)
            ret = set_type(il_value, c.return_type, il_code)
            il_code.add(control_cmds.Return(ret))
        elif self.return_value and c.return_type.is_void():
            err = "function with void return type cannot return value"
            raise CompilerError(err, self.r)
        elif not self.return_value and not c.return_type.is_void():
            err = "function with non-void return type must return value"
            raise CompilerError(err, self.r)
        else:
            il_code.add(control_cmds.Return())


class _BreakContinue(CNode):
    """CNode for a break or continue statement."""

    # "break" if this is a break statement, or "continue" if this is a
    # continue statement
    descrip = None

    def __init__(self):
        """Initialize node."""
        super().__init__()

    def break_target(self, c):
        """Return the label to jump to for this statement, or None if there is
        no enclosing break/continue target.

        Overridden per subclass; dispatched virtually (a plain method, not a
        lambda class attribute, so a base-class `self.break_target(c)` call
        resolves to the concrete subclass at runtime)."""
        return None

    def make_il(self, il_code: "il_gen.ILCode", symbol_table: "il_gen.SymbolTable", c):
        """Make IL code for returning this value."""
        label = self.break_target(c)
        if label:
            il_code.add(control_cmds.Jump(label))
        else:
            with report_err():
                err = f"{self.descrip} statement not in loop"
                raise CompilerError(err, self.r)


class Break(_BreakContinue):
    """CNode for a break statement."""

    descrip = "break"

    def break_target(self, c):
        return c.break_label


class Continue(_BreakContinue):
    """CNode for a continue statement."""

    descrip = "continue"

    def break_target(self, c):
        return c.continue_label


class IfStatement(CNode):
    """CNode for an if-statement.

    cond - Conditional expression of the if-statement.
    stat - Body of the if-statement.
    else_statement - Body of the else-statement, or None.

    """

    def __init__(self, cond, stat, else_stat):
        """Initialize node."""
        super().__init__()

        self.cond = cond
        self.stat = stat
        self.else_stat = else_stat

    def make_il(self, il_code: "il_gen.ILCode", symbol_table: "il_gen.SymbolTable", c):
        """Make code for this if statement."""

        endif_label = il_code.get_label()
        with report_err():
            cond = self.cond.make_il(il_code, symbol_table, c)
            il_code.add(control_cmds.JumpZero(cond, endif_label))

        with report_err():
            self.stat.make_il(il_code, symbol_table, c)

        if self.else_stat:
            end_label = il_code.get_label()
            il_code.add(control_cmds.Jump(end_label))
            il_code.add(control_cmds.Label(endif_label))
            with report_err():
                self.else_stat.make_il(il_code, symbol_table, c)
            il_code.add(control_cmds.Label(end_label))
        else:
            il_code.add(control_cmds.Label(endif_label))


class WhileStatement(CNode):
    """CNode for a while statement.

    cond - Conditional expression of the while-statement.
    stat - Body of the while-statement.

    """

    def __init__(self, cond, stat):
        """Initialize node."""
        super().__init__()
        self.cond = cond
        self.stat = stat

    def make_il(self, il_code: "il_gen.ILCode", symbol_table: "il_gen.SymbolTable", c):
        """Make code for this node."""
        start = il_code.get_label()
        end = il_code.get_label()

        il_code.add(control_cmds.Label(start))
        c = c.set_continue(start).set_break(end)

        with report_err():
            cond = self.cond.make_il(il_code, symbol_table, c)
            il_code.add(control_cmds.JumpZero(cond, end))

        with report_err():
            self.stat.make_il(il_code, symbol_table, c)

        il_code.add(control_cmds.Jump(start))
        il_code.add(control_cmds.Label(end))


class ForStatement(CNode):
    """CNode for a for statement.

    first - First clause of the for-statement, or None if not provided.
    second - Second clause of the for-statement, or None if not provided.
    third - Third clause of the for-statement, or None if not provided.
    stat - Body of the for-statement
    """

    def __init__(self, first, second, third, stat):
        """Initialize node."""
        super().__init__()
        self.first = first
        self.second = second
        self.third = third
        self.stat = stat

    def make_il(self, il_code: "il_gen.ILCode", symbol_table: "il_gen.SymbolTable", c):
        """Make code for this node."""
        symbol_table.new_scope()
        if self.first:
            self.first.make_il(il_code, symbol_table, c)

        start = il_code.get_label()
        cont = il_code.get_label()
        end = il_code.get_label()
        c = c.set_continue(cont).set_break(end)

        il_code.add(control_cmds.Label(start))
        with report_err():
            if self.second:
                cond = self.second.make_il(il_code, symbol_table, c)
                il_code.add(control_cmds.JumpZero(cond, end))

        with report_err():
            self.stat.make_il(il_code, symbol_table, c)

        il_code.add(control_cmds.Label(cont))

        with report_err():
            if self.third:
                self.third.make_il(il_code, symbol_table, c)

        il_code.add(control_cmds.Jump(start))
        il_code.add(control_cmds.Label(end))

        symbol_table.end_scope()


class DoWhileStatement(CNode):
    """CNode for a do-while statement.

    cond - Conditional expression checked after each iteration.
    stat - Body of the loop, executed at least once.
    """

    def __init__(self, cond, stat):
        """Initialize node."""
        super().__init__()
        self.cond = cond
        self.stat = stat

    def make_il(self, il_code: "il_gen.ILCode", symbol_table: "il_gen.SymbolTable", c):
        """Make code for this node."""
        start = il_code.get_label()
        cont = il_code.get_label()
        end = il_code.get_label()

        il_code.add(control_cmds.Label(start))
        # `continue` jumps to the condition test; `break` jumps past the loop.
        c = c.set_continue(cont).set_break(end)

        with report_err():
            self.stat.make_il(il_code, symbol_table, c)

        il_code.add(control_cmds.Label(cont))
        with report_err():
            cond = self.cond.make_il(il_code, symbol_table, c)
            il_code.add(control_cmds.JumpZero(cond, end))

        il_code.add(control_cmds.Jump(start))
        il_code.add(control_cmds.Label(end))


class _SwitchCollector:
    """Collects case values/labels and the default label for a switch."""

    def __init__(self):
        self.cases = []      # list of (constant value, label)
        self.default = None  # label or None


class SwitchStatement(CNode):
    """CNode for a switch statement.

    cond - the controlling expression.
    stat - the switch body.
    """

    def __init__(self, cond, stat):
        """Initialize node."""
        super().__init__()
        self.cond = cond
        self.stat = stat

    def make_il(self, il_code: "il_gen.ILCode", symbol_table: "il_gen.SymbolTable", c):
        """Make code for this node."""
        import shivyc.il_cmds.math as math_cmds
        import shivyc.ctypes as ctypes
        val = self.cond.make_il(il_code, symbol_table, c)
        if not val.ctype.is_integral():
            err = "switch controlling expression must have integer type"
            raise CompilerError(err, self.cond.r)

        # C requires integer promotion on the controlling expression (6.8.4.2);
        # the dispatch chain below subtracts each case value from it, and a
        # sub-int width (char/short/_Bool) makes that subtract/compare misbehave
        # on the backend. Promote so the comparison happens in `int`.
        if val.ctype.size < ctypes.integer.size:
            val = set_type(val, ctypes.integer, il_code)

        dispatch = il_code.get_label()
        end = il_code.get_label()
        collector = _SwitchCollector()

        # Jump to the dispatch chain (emitted after the body), which compares
        # the controlling value against each case and jumps back up to the
        # matching case label. This lets case labels be discovered during the
        # body's own emission (single pass) while keeping fall-through order.
        il_code.add(control_cmds.Jump(dispatch))

        body_c = c.set_break(end).set_switch(collector)
        with report_err():
            self.stat.make_il(il_code, symbol_table, body_c)
        il_code.add(control_cmds.Jump(end))

        il_code.add(control_cmds.Label(dispatch))
        for case_val, label in collector.cases:
            diff = ILValue(val.ctype)
            cval = ILValue(val.ctype)
            il_code.register_literal_var(cval, str(case_val))
            il_code.add(math_cmds.Subtr(diff, val, cval))
            il_code.add(control_cmds.JumpZero(diff, label))
        if collector.default is not None:
            il_code.add(control_cmds.Jump(collector.default))
        il_code.add(control_cmds.Label(end))


class CaseStatement(CNode):
    """CNode for a `case CONST:` labeled statement."""

    def __init__(self, expr, stat):
        """Initialize node."""
        super().__init__()
        self.expr = expr
        self.stat = stat

    def make_il(self, il_code: "il_gen.ILCode", symbol_table: "il_gen.SymbolTable", c):
        """Make code for this node."""
        if c.switch is None:
            err = "'case' label not within a switch statement"
            raise CompilerError(err, self.r)
        val = self.expr.make_il(il_code, symbol_table, c)
        if not val.literal:
            err = "case label must be a compile-time integer constant"
            raise CompilerError(err, self.expr.r)
        label = il_code.get_label()
        c.switch.cases.append((val.literal.val, label))
        il_code.add(control_cmds.Label(label))
        self.stat.make_il(il_code, symbol_table, c)


class DefaultStatement(CNode):
    """CNode for a `default:` labeled statement."""

    def __init__(self, stat):
        """Initialize node."""
        super().__init__()
        self.stat = stat

    def make_il(self, il_code: "il_gen.ILCode", symbol_table: "il_gen.SymbolTable", c):
        """Make code for this node."""
        if c.switch is None:
            err = "'default' label not within a switch statement"
            raise CompilerError(err, self.r)
        label = il_code.get_label()
        c.switch.default = label
        il_code.add(control_cmds.Label(label))
        self.stat.make_il(il_code, symbol_table, c)


class LabelStatement(CNode):
    """CNode for a labeled statement: `name: STMT`."""

    def __init__(self, name: "Token", stat):
        """Initialize node. `name` is the label identifier token."""
        super().__init__()
        self.name = name
        self.stat = stat

    def make_il(self, il_code: "il_gen.ILCode", symbol_table: "il_gen.SymbolTable", c):
        """Make code for this node."""
        if c.labels is None:
            err = "label not within a function"
            raise CompilerError(err, self.r)
        key = self.name.content
        if key not in c.labels:
            c.labels[key] = il_code.get_label()
        il_code.add(control_cmds.Label(c.labels[key]))
        self.stat.make_il(il_code, symbol_table, c)


class GotoStatement(CNode):
    """CNode for a `goto name;` statement."""

    def __init__(self, name: "Token"):
        """Initialize node. `name` is the target label identifier token."""
        super().__init__()
        self.name = name

    def make_il(self, il_code: "il_gen.ILCode", symbol_table: "il_gen.SymbolTable", c):
        """Make code for this node."""
        if c.labels is None:
            err = "goto not within a function"
            raise CompilerError(err, self.r)
        key = self.name.content
        # Labels are forward-referenceable, so create the target on first use.
        if key not in c.labels:
            c.labels[key] = il_code.get_label()
        il_code.add(control_cmds.Jump(c.labels[key]))
