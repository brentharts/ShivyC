"""Base classes for AST nodes."""


class Node:
    """Base class for representing a single node in the AST.

    All AST nodes inherit from this class.
    """

    def __init__(self):
        """Initialize node."""

        # Set range to None because it will be set by the parser.
        self.r = None

    def make_il(self, il_code, symbol_table, c):
        """Generate IL code for this node.

        il_code - ILCode object to add generated code to.
        symbol_table - Symbol table for current node.
        c - Context for current node, as above. This function should not
        modify this object.
        """
        raise NotImplementedError

    def make_il_raw(self, il_code, symbol_table, c):
        """Generate IL code for this node without any lvalue-to-rvalue
        conversion. Declared here so it is part of the root virtual interface
        (and thus a vtable slot) for the expression-node hierarchy.
        """
        raise NotImplementedError

    def stmt_expr_value(self, il_code, symbol_table, c):
        """Run this statement and return its value as a GCC statement-expression
        result: the contained expression's value for an expression statement,
        and None for any other statement.

        Declared on the root so it is dispatched virtually by the self-hosted
        compiler. StmtExpr calls this on its trailing item *outside* any
        report_err() frame, because a value assigned inside that frame's
        setjmp/longjmp scope is not reliably preserved once control leaves it.
        Doing the access through this override also keeps `self.expr` a direct,
        statically-typed field read rather than a dynamic attribute lookup.
        """
        self.make_il(il_code, symbol_table, c)
        return None
