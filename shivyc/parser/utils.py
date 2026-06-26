"""Utilities for the parser."""

from __future__ import annotations

from contextlib import contextmanager

from shivyc.errors import CompilerError, Range
from shivyc.tree.base_nodes import Node


# This is a little bit messy, but worth the repetition it saves. In the
# parser.py file, the main parse function sets this global variable to the
# list of tokens. Then, all functions in the parser can reference this
# variable rather than passing around the tokens list everywhere.
tokens: "list | None" = None

# Name of the function whose body is currently being parsed, or None at file
# scope. Used to resolve the C99 predefined identifier __func__.
cur_func_name: "str | None" = None


class SimpleSymbolTable:
    """Table to record every declared symbol.

    This is required to parse typedefs in C, because the parser must know
    whether a given identifier denotes a type or a value. For every
    declared identifier, the table records whether or not it is a type
    defnition.
    """
    def __init__(self) -> None:
        self.symbols: list = []
        # Undo journal of mutations since program start. snapshot() records a
        # position in this journal and restore() replays the entries logged
        # after it, in reverse. This makes a speculative-parse backup O(1) and
        # its rollback O(changes), rather than copying the whole symbol table
        # (whose file scope grows with every top-level declaration) on every
        # speculative parse -- which was quadratic in the number of
        # declarations and dominated parser peak memory on large inputs.
        self.journal: list = []
        self.new_scope()

    def new_scope(self) -> None:
        self.symbols.append({})
        self.journal.append(["s", None, None, False, False])

    def end_scope(self) -> None:
        popped = self.symbols.pop()
        self.journal.append(["e", popped, None, False, False])

    def add_symbol(self, identifier, is_typedef: bool) -> None:
        scope = self.symbols[-1]
        # Use identifier.content inline (as an object) rather than via a local:
        # the transpiler would type a `key = identifier.content` local as a C
        # string and convert a None content to a null pointer, which then
        # crashes string comparison. Inline, a None content stays a None object
        # and simply never matches a real key (as in the pre-journal code).
        existed: bool = identifier.content in scope
        old_val = scope[identifier.content] if existed else False
        self.journal.append(
            ["a", scope, identifier.content, existed, old_val])
        scope[identifier.content] = is_typedef

    def is_typedef(self, identifier) -> bool:
        name: str = identifier.content
        for table in self.symbols[::-1]:
            if name in table:
                return table[name]
        return False

    def snapshot(self) -> list:
        """Return a cheap, restorable mark of the current table state.

        The mark is the current length of the undo journal (boxed in a
        one-element list so the self-hosting transpiler tracks it as an object
        consistently). Restoring to it replays and discards every mutation
        logged since -- avoiding the former whole-table copy, whose file scope
        grows with the program, on every speculative parse.
        """
        return [len(self.journal)]

    def restore(self, snap: list) -> None:
        """Restore table state to a snapshot mark, in place.

        Undo every mutation logged after the mark, most recent first. Symbol
        additions are keyed to the scope dict object they touched, so they undo
        correctly regardless of intervening scope pushes/pops.
        """
        mark = snap[0]
        while len(self.journal) > mark:
            entry = self.journal.pop()
            op = entry[0]
            if op == "a":
                scope = entry[1]
                key = entry[2]
                if entry[3]:
                    scope[key] = entry[4]
                else:
                    del scope[key]
            elif op == "s":
                self.symbols.pop()
            else:
                self.symbols.append(entry[1])


symbols = SimpleSymbolTable()


class ParserError(CompilerError):
    """Class representing parser errors.

    amount_parsed (int) - Number of tokens successfully parsed before this
    error was encountered. This value is used by the Parser to determine which
    error corresponds to the most successful parse.
    """

    # Options for the message_type constructor field.
    #
    # AT generates a message like "expected semicolon at '}'", GOT generates a
    # message like "expected semicolon, got '}'", and AFTER generates a message
    # like "expected semicolon after '15'" (if possible).
    #
    # As a very general guide, use AT when a token should be removed, use AFTER
    # when a token should be to be inserted (esp. because of what came before),
    # and GOT when a token should be changed.
    AT = 1
    GOT = 2
    AFTER = 3

    def __init__(self, message, index, tokens, message_type):
        """Initialize a ParserError from the given arguments.

        message (str) - Base message to put in the error.
        tokens (List[Token]) - List of tokens.
        index (int) - Index of the offending token.
        message_type (int) - One of self.AT, self.GOT, or self.AFTER.

        Example:
            ParserError("unexpected semicolon", 10, [...], self.AT)
               -> CompilerError("unexpected semicolon at ';'", ..., ...)
               -> "main.c:10: unexpected semicolon at ';'"
        """
        self.amount_parsed = index

        if len(tokens) == 0:
            super().__init__(f"{message} at beginning of source")
            return

        # If the index is too big, we're always using the AFTER form
        if index >= len(tokens):
            index = len(tokens)
            message_type = self.AFTER
        # If the index is too small, we should not use the AFTER form
        elif index <= 0:
            index = 0
            if message_type == self.AFTER:
                message_type = self.GOT

        if message_type == self.AT:
            super().__init__(f"{message} at '{tokens[index]}'",
                             tokens[index].r)
        elif message_type == self.GOT:
            super().__init__(f"{message}, got '{tokens[index]}'",
                             tokens[index].r)
        elif message_type == self.AFTER:
            if tokens[index - 1].r:
                new_range = Range(tokens[index - 1].r.end + 1)
            else:
                new_range = None

            super().__init__(
                f"{message} after '{tokens[index - 1]}'", new_range)


def raise_error(err: str, index: int, error_type: int) -> None:
    """Raise a parser error."""
    raise ParserError(err, index, tokens, error_type)


# Used to store the best error found in the parsing phase.
best_error = None


@contextmanager
def log_error():
    """Wrap this context manager around conditional parsing code.

    For example,

    with log_error():
        [try parsing something]
        return

    [try parsing something else]

    will run the code in [try parsing something]. If an error occurs,
    it will be saved and then [try parsing something else] will run.

    The value of e.amount_parsed is used to determine the amount
    successfully parsed before encountering the error.
    """
    global best_error, symbols

    # Back up the symbol table so a failed speculative parse can be undone.
    # A shallow snapshot suffices (see SimpleSymbolTable.snapshot) and replaces
    # the former copy.deepcopy, which recursively cloned the whole table on
    # every speculative parse and dominated parse time.
    symbols_bak: list = symbols.snapshot()
    try:
        yield
    except ParserError as e:
        if not best_error or e.amount_parsed >= best_error.amount_parsed:
            best_error = e
        symbols.restore(symbols_bak)


def token_is(index: int, kind) -> bool:
    """Return true if the next token is of the given kind."""
    return len(tokens) > index and tokens[index].kind == kind


def token_in(index: int, kinds) -> bool:
    """Return true if the next token is in the given list/set of kinds."""
    return len(tokens) > index and tokens[index].kind in kinds


def match_token(index: int, kind, message_type: int,
                message: "str | None" = None) -> int:
    """Raise ParserError if tokens[index] is not of the expected kind.

    If tokens[index] is of the expected kind, returns index + 1.
    Otherwise, raises a ParserError with the given message and
    message_type.

    """
    if not message:
        message = f"expected '{kind.text_repr}'"

    if token_is(index, kind):
        return index + 1
    else:
        raise ParserError(message, index, tokens, message_type)


def token_range(start: int, end: int):
    """Generate a range that encompasses tokens[start] to tokens[end-1]"""
    # An empty translation unit (e.g. a file containing only comments) has no
    # tokens and therefore no source range to point to.
    if not tokens:
        return None
    start_index: int = max(0, min(start, len(tokens) - 1, end - 1))
    end_index: int = max(0, min(end - 1, len(tokens) - 1))
    return tokens[start_index].r + tokens[end_index].r


def add_range(parse_func):
    """Return a decorated function that tags the produced node with a range.

    Accepts a parse_* function, and returns a version of the function where
    the returned node has its range attribute set

    """
    def parse_with_range(index: int, *args):
        start_index: int = index
        node, end_index = parse_func(index, *args)
        # `node` is a parse node that carries a range field. The annotated
        # alias lets the transpiler lower the write below as a struct-field
        # store rather than a dynamic setattr (which it cannot self-host).
        # Under CPython `from __future__ import annotations` makes the
        # annotation an inert string, so this is exactly `node.r = ...`.
        ranged: "Node" = node
        ranged.r = token_range(start_index, end_index)

        return node, end_index

    return parse_with_range
