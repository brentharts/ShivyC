"""Parser logic that parses expression nodes."""

import shivyc.parser.utils as p
import shivyc.token_kinds as token_kinds
import shivyc.tree as tree
import shivyc.tree.decl_nodes as decl_nodes
from shivyc.parser.utils import (add_range, match_token, token_is, ParserError,
                                 raise_error, log_error, token_in)

# Operator-separator tables for parse_series, hoisted to module scope so the
# expression-precedence cascade does not rebuild these constant dicts on every
# (recursive) sub-expression -- a dominant source of parser arena churn.
_SEP_COMMA = {token_kinds.comma: tree.MultiExpr}
_SEP_LOGOR = {token_kinds.bool_or: tree.BoolOr}
_SEP_LOGAND = {token_kinds.bool_and: tree.BoolAnd}
_SEP_BITOR = {token_kinds.bitor: tree.BitOr}
_SEP_BITXOR = {token_kinds.bitxor: tree.BitXor}
_SEP_BITAND = {token_kinds.amp: tree.BitAnd}
_SEP_EQUALITY = {token_kinds.twoequals: tree.Equality, token_kinds.notequal: tree.Inequality}
_SEP_RELATIONAL = {token_kinds.lt: tree.LessThan, token_kinds.gt: tree.GreaterThan, token_kinds.ltoe: tree.LessThanOrEq, token_kinds.gtoe: tree.GreaterThanOrEq}
_SEP_BITWISE = {token_kinds.lbitshift: tree.LBitShift, token_kinds.rbitshift: tree.RBitShift}
_SEP_ADDITIVE = {token_kinds.plus: tree.Plus, token_kinds.minus: tree.Minus}
_SEP_MULTIPLICATIVE = {token_kinds.star: tree.Mult, token_kinds.slash: tree.Div, token_kinds.mod: tree.Mod}


@add_range
def parse_expression(index):
    """Parse expression."""
    return parse_series(
        index, parse_assignment,
        _SEP_COMMA)


@add_range
def parse_assignment(index):
    """Parse an assignment expression."""

    # This is a slight departure from the official grammar. The standard
    # specifies that a program is syntactically correct only if the
    # left-hand side of an assignment expression is a unary expression. But,
    # to provide more helpful error messages, we permit the left side to be
    # any non-assignment expression.

    left, index = parse_conditional(index)

    if index < len(p.tokens):
        op = p.tokens[index]
        kind = op.kind
    else:
        op = None
        kind = None

    node_types = {token_kinds.equals: tree.Equals,
                  token_kinds.plusequals: tree.PlusEquals,
                  token_kinds.minusequals: tree.MinusEquals,
                  token_kinds.starequals: tree.StarEquals,
                  token_kinds.divequals: tree.DivEquals,
                  token_kinds.modequals: tree.ModEquals,
                  token_kinds.orequals: tree.OrEquals,
                  token_kinds.andequals: tree.AndEquals,
                  token_kinds.xorequals: tree.XorEquals,
                  token_kinds.lshiftequals: tree.LShiftEquals,
                  token_kinds.rshiftequals: tree.RShiftEquals}

    if kind in node_types:
        right, index = parse_assignment(index + 1)
        return node_types[kind](left, right, op), index
    else:
        return left, index


@add_range
def parse_conditional(index):
    """Parse a conditional (ternary) expression.

    conditional-expression:
        logical-OR-expression
        logical-OR-expression ? expression : conditional-expression
    """
    cond, index = parse_logical_or(index)
    if token_is(index, token_kinds.question):
        op = p.tokens[index]
        then_expr, index = parse_expression(index + 1)
        index = match_token(index, token_kinds.colon, ParserError.AFTER)
        else_expr, index = parse_conditional(index)
        return tree.Conditional(cond, then_expr, else_expr, op), index
    return cond, index


@add_range
def parse_logical_or(index):
    """Parse logical or expression."""
    return parse_series(
        index, parse_logical_and,
        _SEP_LOGOR)


@add_range
def parse_logical_and(index):
    """Parse logical and expression."""
    return parse_series(
        index, parse_bit_or,
        _SEP_LOGAND)


@add_range
def parse_bit_or(index):
    """Parse bitwise or (|) expression."""
    return parse_series(
        index, parse_bit_xor,
        _SEP_BITOR)


@add_range
def parse_bit_xor(index):
    """Parse bitwise xor (^) expression."""
    return parse_series(
        index, parse_bit_and,
        _SEP_BITXOR)


@add_range
def parse_bit_and(index):
    """Parse bitwise and (&) expression."""
    return parse_series(
        index, parse_equality,
        _SEP_BITAND)


@add_range
def parse_equality(index):
    """Parse equality expression."""
    # TODO: Implement relational and shift expressions here.
    return parse_series(
        index, parse_relational,
        _SEP_EQUALITY)


@add_range
def parse_relational(index):
    """Parse relational expression."""
    return parse_series(
        index, parse_bitwise,
        _SEP_RELATIONAL)


@add_range
def parse_bitwise(index):
    return parse_series(
        index, parse_additive,
        _SEP_BITWISE)


@add_range
def parse_additive(index):
    """Parse additive expression."""
    return parse_series(
        index, parse_multiplicative,
        _SEP_ADDITIVE)


@add_range
def parse_multiplicative(index):
    """Parse multiplicative expression."""
    return parse_series(
        index, parse_cast,
        _SEP_MULTIPLICATIVE)


@add_range
def parse_cast(index):
    """Parse cast expression."""

    from shivyc.parser.declaration import (
        parse_abstract_declarator, parse_spec_qual_list)

    with log_error():
        start = index
        match_token(index, token_kinds.open_paren, ParserError.AT)
        specs, index = parse_spec_qual_list(index + 1)
        node, index = parse_abstract_declarator(index)
        match_token(index, token_kinds.close_paren, ParserError.AT)

        decl_node = decl_nodes.Root(specs, [node])

        # A '{' after the parenthesized type-name makes this a C99 compound
        # literal, not a cast.
        if token_is(index + 1, token_kinds.open_brack):
            from shivyc.parser.declaration import parse_initializer
            init, index = parse_initializer(index + 1)
            cl = tree.CompoundLiteral(decl_node, init)
            # A compound literal is a postfix-expression operand, so it may be
            # subscripted, called, or have a member accessed (e.g. musl's
            # `(size_t[3]){0,a,b}[whence]`). Give it a range before applying
            # postfix operators, which read it.
            cl.r = p.tokens[start].r + p.tokens[index - 1].r
            return _parse_postfix_ops(cl, index)

        expr_node, index = parse_cast(index + 1)
        return tree.Cast(decl_node, expr_node), index

    return parse_unary(index)


@add_range
def parse_unary(index):
    """Parse unary expression."""

    unary_args = {token_kinds.incr: (parse_unary, tree.PreIncr),
                  token_kinds.decr: (parse_unary, tree.PreDecr),
                  token_kinds.amp: (parse_cast, tree.AddrOf),
                  token_kinds.star: (parse_cast, tree.Deref),
                  token_kinds.bool_not: (parse_cast, tree.BoolNot),
                  token_kinds.plus: (parse_cast, tree.UnaryPlus),
                  token_kinds.minus: (parse_cast, tree.UnaryMinus),
                  token_kinds.compl: (parse_cast, tree.Compl)}

    if token_in(index, unary_args):
        parse_func, NodeClass = unary_args[p.tokens[index].kind]
        subnode, index = parse_func(index + 1)
        return NodeClass(subnode), index
    elif token_is(index, token_kinds.sizeof_kw):
        with log_error():
            node, index = parse_unary(index + 1)
            return tree.SizeofExpr(node), index

        from shivyc.parser.declaration import (
            parse_abstract_declarator, parse_spec_qual_list)

        match_token(index + 1, token_kinds.open_paren, ParserError.AFTER)
        specs, index = parse_spec_qual_list(index + 2)
        node, index = parse_abstract_declarator(index)
        match_token(index, token_kinds.close_paren, ParserError.AT)
        decl_node = decl_nodes.Root(specs, [node])

        return tree.SizeofType(decl_node), index + 1
    elif token_is(index, token_kinds.alignof_kw):
        # C11 _Alignof requires a parenthesized type-name; we also accept an
        # expression operand (the GCC __alignof__ form) for leniency.
        from shivyc.parser.declaration import (
            parse_abstract_declarator, parse_spec_qual_list)

        match_token(index + 1, token_kinds.open_paren, ParserError.AFTER)
        with log_error():
            specs, after = parse_spec_qual_list(index + 2)
            node, after = parse_abstract_declarator(after)
            after = match_token(after, token_kinds.close_paren,
                                ParserError.AT)
            decl_node = decl_nodes.Root(specs, [node])
            return tree.AlignofType(decl_node), after

        node, index = parse_unary(index + 1)
        return tree.AlignofExpr(node), index
    elif (token_is(index, token_kinds.identifier)
          and p.tokens[index].content == "__builtin_va_arg"):
        from shivyc.parser.declaration import (
            parse_abstract_declarator, parse_spec_qual_list)

        match_token(index + 1, token_kinds.open_paren, ParserError.AFTER)
        ap_node, index = parse_assignment(index + 2)
        index = match_token(index, token_kinds.comma, ParserError.AFTER)
        specs, index = parse_spec_qual_list(index)
        node, index = parse_abstract_declarator(index)
        index = match_token(index, token_kinds.close_paren, ParserError.AT)
        decl_node = decl_nodes.Root(specs, [node])
        return tree.VaArg(ap_node, decl_node), index
    elif (token_is(index, token_kinds.identifier)
          and p.tokens[index].content == "__builtin_offsetof"):
        from shivyc.parser.declaration import (
            parse_abstract_declarator, parse_spec_qual_list)

        match_token(index + 1, token_kinds.open_paren, ParserError.AFTER)
        specs, index = parse_spec_qual_list(index + 2)
        node, index = parse_abstract_declarator(index)
        decl_node = decl_nodes.Root(specs, [node])
        index = match_token(index, token_kinds.comma, ParserError.AFTER)

        # Member designator: identifier ( .identifier | [expr] )*
        designator = []
        ident = p.tokens[index]
        if ident.kind is not token_kinds.identifier:
            raise_error("expected member name in __builtin_offsetof",
                        index, ParserError.AT)
        designator.append(("member", ident.content))
        index += 1
        while True:
            if token_is(index, token_kinds.dot):
                m = p.tokens[index + 1]
                if m.kind is not token_kinds.identifier:
                    raise_error("expected member name after '.'",
                                index + 1, ParserError.AT)
                designator.append(("member", m.content))
                index += 2
            elif token_is(index, token_kinds.open_sq_brack):
                expr, index = parse_expression(index + 1)
                index = match_token(
                    index, token_kinds.close_sq_brack, ParserError.GOT)
                designator.append(("index", expr))
            else:
                break

        index = match_token(index, token_kinds.close_paren, ParserError.GOT)
        return tree.OffsetofType(decl_node, designator), index
    else:
        return parse_postfix(index)


@add_range
def parse_postfix(index):
    """Parse postfix expression."""
    cur, index = parse_primary(index)
    return _parse_postfix_ops(cur, index)


def _parse_postfix_ops(cur, index):
    """Apply any trailing postfix operators ([], ., ->, (), ++, --) to an
    already-parsed primary expression `cur`. Shared by parse_postfix and by
    compound literals (which may also be subscripted, e.g. `(int[]){..}[i]`)."""
    while True:
        old_range = cur.r

        if token_is(index, token_kinds.open_sq_brack):
            index += 1
            arg, index = parse_expression(index)
            cur = tree.ArraySubsc(cur, arg)
            match_token(index, token_kinds.close_sq_brack, ParserError.GOT)
            index += 1

        elif (token_is(index, token_kinds.dot)
              or token_is(index, token_kinds.arrow)):
            index += 1
            match_token(index, token_kinds.identifier, ParserError.AFTER)
            member = p.tokens[index]

            if token_is(index - 1, token_kinds.dot):
                cur = tree.ObjMember(cur, member)
            else:
                cur = tree.ObjPtrMember(cur, member)

            index += 1

        elif token_is(index, token_kinds.open_paren):
            args = []
            index += 1

            # Recognize the va_start address builtin: __builtin_va_start_addr()
            if (isinstance(cur, tree.Identifier)
                    and cur.identifier.content == "__builtin_va_start_addr"
                    and token_is(index, token_kinds.close_paren)):
                node = tree.VaStartAddr()
                node.r = old_range + p.tokens[index].r
                return node, index + 1

            if token_is(index, token_kinds.close_paren):
                index += 1
            else:
                while True:
                    arg, index = parse_assignment(index)
                    args.append(arg)

                    if token_is(index, token_kinds.comma):
                        index += 1
                    else:
                        break

                index = match_token(
                    index, token_kinds.close_paren, ParserError.GOT)

            # Set cur and continue the loop so postfix operators can follow a
            # call (e.g. f()->m, f()[i], f().m, callbacks like f()()).
            cur = tree.FuncCall(cur, args)

        elif token_is(index, token_kinds.incr):
            index += 1
            cur = tree.PostIncr(cur)
        elif token_is(index, token_kinds.decr):
            index += 1
            cur = tree.PostDecr(cur)
        else:
            return cur, index

        cur.r = old_range + p.tokens[index - 1].r


@add_range
def parse_primary(index):
    """Parse primary expression."""
    if token_is(index, token_kinds.open_paren):
        # GCC statement expression: ({ statements... })
        if token_is(index + 1, token_kinds.open_brack):
            from shivyc.parser.statement import parse_compound_statement
            compound, index = parse_compound_statement(index + 1)
            index = match_token(index, token_kinds.close_paren, ParserError.GOT)
            return tree.StmtExpr(compound.items), index
        node, index = parse_expression(index + 1)
        index = match_token(index, token_kinds.close_paren, ParserError.GOT)
        return tree.ParenExpr(node), index
    elif token_is(index, token_kinds.number):
        return tree.Number(p.tokens[index]), index + 1
    elif (token_is(index, token_kinds.identifier)
          and not p.symbols.is_typedef(p.tokens[index])):
        name = p.tokens[index].content
        # C99 __func__ (and the GCC aliases) behave like a static const char[]
        # holding the enclosing function's name.
        if name in ("__func__", "__FUNCTION__", "__PRETTY_FUNCTION__"):
            fname = p.cur_func_name or ""
            chars = [ord(c) for c in fname] + [0]
            return tree.String(chars), index + 1
        return tree.Identifier(p.tokens[index]), index + 1
    elif token_is(index, token_kinds.string):
        return (tree.String(p.tokens[index].content, p.tokens[index].wide),
                index + 1)
    elif token_is(index, token_kinds.char_string):
        chars = p.tokens[index].content
        if len(chars) == 1:
            value = chars[0]
        else:
            # Multi-character constant (C11 6.4.4.4p10): implementation-defined
            # value. Match gcc -- pack bytes big-endian into an int, keep the
            # low 32 bits, interpreted as a signed int. e.g. 'ab' -> 0x6162.
            packed = 0
            for ch in chars:
                packed = ((packed << 8) | (ch & 0xFF)) & 0xFFFFFFFF
            value = packed - 0x100000000 if packed >= 0x80000000 else packed
        return tree.Number(value), index + 1
    else:
        raise_error("expected expression", index, ParserError.GOT)


def parse_series(index, parse_base, separators):
    """Parse a series of symbols joined together with given separator(s).

    index (int) - Index at which to start searching.
    parse_base (function) - A parse_* function that parses the base symbol.
    separators (Dict(TokenKind -> Node)) - The separators that join
    instances of the base symbol. Each separator corresponds to a Node,
    which is the Node produced to join two expressions connected with that
    separator.
    """
    cur, index = parse_base(index)
    while True:
        for s in separators:
            if token_is(index, s):
                break
        else:
            return cur, index

        tok = p.tokens[index]
        new, index = parse_base(index + 1)
        cur = separators[s](cur, new, tok)
