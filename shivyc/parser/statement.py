"""Parser logic that parses statement nodes."""

import shivyc.token_kinds as token_kinds
import shivyc.tree.general_nodes as general_nodes
import shivyc.parser.utils as p

from shivyc.parser.declaration import parse_declaration
from shivyc.parser.expression import parse_expression
from shivyc.parser.utils import (add_range, log_error, match_token, token_is,
                                 ParserError)
from shivyc.tree import (Return, Break, Continue, IfStatement, WhileStatement,
                         ForStatement, DoWhileStatement, SwitchStatement,
                         CaseStatement, DefaultStatement, LabelStatement,
                         GotoStatement)


@add_range
def parse_statement(index):
    """Parse a statement.

    Try each possible type of statement, catching/logging exceptions upon
    parse failures. On the last try, raise the exception on to the caller.

    """
    for func in (parse_compound_statement, parse_return, parse_break,
                 parse_continue, parse_if_statement, parse_while_statement,
                 parse_do_while_statement, parse_for_statement,
                 parse_switch_statement, parse_case_statement,
                 parse_default_statement, parse_goto_statement,
                 parse_label_statement, parse_inline_asm):
        with log_error():
            return func(index)

    return parse_expr_statement(index)


@add_range
def parse_compound_statement(index):
    """Parse a compound statement.

    A compound statement is a collection of several
    statements/declarations, enclosed in braces.

    """
    p.symbols.new_scope()
    index = match_token(index, token_kinds.open_brack, ParserError.GOT)

    # Read block items (statements/declarations) until there are no more.
    items = []
    while True:
        with log_error():
            item, index = parse_statement(index)
            items.append(item)
            continue

        with log_error():
            item, index = parse_declaration(index)
            items.append(item)
            continue

        break

    index = match_token(index, token_kinds.close_brack, ParserError.GOT)
    p.symbols.end_scope()

    return general_nodes.Compound(items), index


@add_range
def parse_return(index):
    """Parse a return statement.

    Ex: return 5;

    """
    index = match_token(index, token_kinds.return_kw, ParserError.GOT)
    if token_is(index, token_kinds.semicolon):
        return Return(None), index

    node, index = parse_expression(index)

    index = match_token(index, token_kinds.semicolon, ParserError.AFTER)
    return Return(node), index


@add_range
def parse_break(index):
    """Parse a break statement."""
    index = match_token(index, token_kinds.break_kw, ParserError.GOT)
    index = match_token(index, token_kinds.semicolon, ParserError.AFTER)
    return Break(), index


@add_range
def parse_continue(index):
    """Parse a continue statement."""
    index = match_token(index, token_kinds.continue_kw, ParserError.GOT)
    index = match_token(index, token_kinds.semicolon, ParserError.AFTER)
    return Continue(), index


@add_range
def parse_if_statement(index):
    """Parse an if statement."""
    index = match_token(index, token_kinds.if_kw, ParserError.GOT)
    index = match_token(index, token_kinds.open_paren, ParserError.AFTER)
    conditional, index = parse_expression(index)
    index = match_token(index, token_kinds.close_paren, ParserError.AFTER)
    statement, index = parse_statement(index)

    # If there is an else that follows, parse that too.
    is_else = token_is(index, token_kinds.else_kw)
    if not is_else:
        else_statement = None
    else:
        index = match_token(index, token_kinds.else_kw, ParserError.GOT)
        else_statement, index = parse_statement(index)

    return IfStatement(conditional, statement, else_statement), index


@add_range
def parse_while_statement(index):
    """Parse a while statement."""
    index = match_token(index, token_kinds.while_kw, ParserError.GOT)
    index = match_token(index, token_kinds.open_paren, ParserError.AFTER)
    conditional, index = parse_expression(index)
    index = match_token(index, token_kinds.close_paren, ParserError.AFTER)
    statement, index = parse_statement(index)

    return WhileStatement(conditional, statement), index


@add_range
def parse_do_while_statement(index):
    """Parse a do-while statement: do STMT while ( EXPR ) ;"""
    index = match_token(index, token_kinds.do_kw, ParserError.GOT)
    statement, index = parse_statement(index)
    index = match_token(index, token_kinds.while_kw, ParserError.AFTER)
    index = match_token(index, token_kinds.open_paren, ParserError.AFTER)
    conditional, index = parse_expression(index)
    index = match_token(index, token_kinds.close_paren, ParserError.AFTER)
    index = match_token(index, token_kinds.semicolon, ParserError.AFTER)

    return DoWhileStatement(conditional, statement), index


@add_range
def parse_switch_statement(index):
    """Parse a switch statement: switch ( EXPR ) STMT"""
    index = match_token(index, token_kinds.switch_kw, ParserError.GOT)
    index = match_token(index, token_kinds.open_paren, ParserError.AFTER)
    cond, index = parse_expression(index)
    index = match_token(index, token_kinds.close_paren, ParserError.AFTER)
    stat, index = parse_statement(index)
    return SwitchStatement(cond, stat), index


@add_range
def parse_case_statement(index):
    """Parse a case label: case CONST : STMT"""
    from shivyc.parser.expression import parse_conditional
    index = match_token(index, token_kinds.case_kw, ParserError.GOT)
    expr, index = parse_conditional(index)
    index = match_token(index, token_kinds.colon, ParserError.AFTER)
    stat, index = parse_statement(index)
    return CaseStatement(expr, stat), index


@add_range
def parse_default_statement(index):
    """Parse a default label: default : STMT"""
    index = match_token(index, token_kinds.default_kw, ParserError.GOT)
    index = match_token(index, token_kinds.colon, ParserError.AFTER)
    stat, index = parse_statement(index)
    return DefaultStatement(stat), index


@add_range
def parse_goto_statement(index):
    """Parse a goto statement: goto IDENTIFIER ;"""
    index = match_token(index, token_kinds.goto_kw, ParserError.GOT)
    name = p.tokens[index]
    index = match_token(index, token_kinds.identifier, ParserError.AFTER)
    index = match_token(index, token_kinds.semicolon, ParserError.AFTER)
    return GotoStatement(name), index


@add_range
def parse_label_statement(index):
    """Parse a labeled statement: IDENTIFIER : STMT"""
    if not (token_is(index, token_kinds.identifier)
            and token_is(index + 1, token_kinds.colon)):
        p.raise_error("expected label", index, ParserError.AT)
    name = p.tokens[index]
    index = match_token(index + 1, token_kinds.colon, ParserError.GOT)
    # A label normally precedes a statement, but C23 and GCC also allow a
    # declaration here (e.g. `done: int x = 0;`). Try a statement first, then
    # fall back to a declaration.
    with log_error():
        stat, after = parse_statement(index)
        return LabelStatement(name, stat), after
    from shivyc.parser.declaration import parse_declaration
    stat, index = parse_declaration(index)
    return LabelStatement(name, stat), index


@add_range
def parse_for_statement(index):
    """Parse a for statement."""
    index = match_token(index, token_kinds.for_kw, ParserError.GOT)
    index = match_token(index, token_kinds.open_paren, ParserError.AFTER)

    first, second, third, index = _get_for_clauses(index)
    stat, index = parse_statement(index)

    return ForStatement(first, second, third, stat), index


def _get_for_clauses(index):
    """Get the three clauses of a for-statement.

    index - Index of the beginning of the first clause.

    returns - Tuple (Node, Node, Node, index). Each Node is the corresponding
    clause, or None if that clause is empty The index is that of first token
    after the close paren terminating the for clauses.

    Raises exception on malformed input.
    """

    first, index = _get_first_for_clause(index)

    if token_is(index, token_kinds.semicolon):
        second = None
        index += 1
    else:
        second, index = parse_expression(index)
        index = match_token(index, token_kinds.semicolon, ParserError.AFTER)

    if token_is(index, token_kinds.close_paren):
        third = None
        index += 1
    else:
        third, index = parse_expression(index)
        index = match_token(index, token_kinds.close_paren, ParserError.AFTER)

    return first, second, third, index


def _get_first_for_clause(index):
    """Get the first clause of a for-statement.

    index - Index of the beginning of the first clause in the for-statement.
    returns - Tuple. First element is a node if a clause is found and None if
    there is no clause (i.e. semicolon terminating the clause). Second element
    is an integer index where the next token begins.

    If malformed, raises exception.

    """
    if token_is(index, token_kinds.semicolon):
        return None, index + 1

    with log_error():
        return parse_declaration(index)

    clause, index = parse_expression(index)
    index = match_token(index, token_kinds.semicolon, ParserError.AFTER)
    return clause, index


@add_range
def parse_expr_statement(index):
    """Parse a statement that is an expression.

    Ex: a = 3 + 4

    """
    if token_is(index, token_kinds.semicolon):
        return general_nodes.EmptyStatement(), index + 1

    node, index = parse_expression(index)
    index = match_token(index, token_kinds.semicolon, ParserError.AFTER)
    return general_nodes.ExprStatement(node), index


def _asm_string(index):
    """Read one (or several adjacent) asm string-literal tokens as text."""
    if not token_is(index, token_kinds.string):
        p.raise_error("expected string in asm statement", index, ParserError.AT)
    chars = []
    while token_is(index, token_kinds.string):
        # String token content is a list of char codes ending in NUL.
        body = list(p.tokens[index].content)
        if body and body[-1] == 0:
            body = body[:-1]
        chars.extend(body)
        index += 1
    return "".join(chr(c) for c in chars), index


def _asm_operands(index):
    """Parse a (possibly empty) comma list of `"constraint" ( expr )`."""
    ops = []
    while token_is(index, token_kinds.string):
        constraint, index = _asm_string(index)
        index = match_token(index, token_kinds.open_paren, ParserError.AFTER)
        expr, index = parse_expression(index)
        index = match_token(index, token_kinds.close_paren, ParserError.GOT)
        ops.append((constraint, expr))
        if token_is(index, token_kinds.comma):
            index += 1
        else:
            break
    return ops, index


def _asm_clobbers(index):
    """Parse a (possibly empty) comma list of clobber strings."""
    clobbers = []
    while token_is(index, token_kinds.string):
        name, index = _asm_string(index)
        clobbers.append(name)
        if token_is(index, token_kinds.comma):
            index += 1
        else:
            break
    return clobbers, index


@add_range
def parse_inline_asm(index):
    """Parse `asm [volatile] ( template [: out [: in [: clobbers]]] ) ;`.

    Recognized when the `asm` keyword (or `__asm__`, rewritten to `asm` in the
    preprocessor prelude), optionally followed by `volatile`, is followed by
    `(` and a string literal, so it does not capture ordinary statements.
    """
    if not token_is(index, token_kinds.asm_kw):
        p.raise_error("not an asm statement", index, ParserError.AT)
    kw_index = index
    index += 1
    # Optional GNU `volatile` qualifier. (`__volatile__`/`__volatile` and
    # `inline` are erased by the preprocessor prelude, so only the bare
    # `volatile` keyword can appear here.)
    if token_is(index, token_kinds.volatile_kw):
        index += 1
    if not (token_is(index, token_kinds.open_paren)
            and token_is(index + 1, token_kinds.string)):
        p.raise_error("not an asm statement", kw_index, ParserError.AT)

    index = match_token(index, token_kinds.open_paren, ParserError.AFTER)
    template, index = _asm_string(index)

    outputs, inputs, clobbers = [], [], []
    if token_is(index, token_kinds.colon):
        outputs, index = _asm_operands(index + 1)
        if token_is(index, token_kinds.colon):
            inputs, index = _asm_operands(index + 1)
            if token_is(index, token_kinds.colon):
                clobbers, index = _asm_clobbers(index + 1)

    index = match_token(index, token_kinds.close_paren, ParserError.GOT)
    index = match_token(index, token_kinds.semicolon, ParserError.AFTER)
    return general_nodes.InlineAsm(template, outputs, inputs, clobbers), index
