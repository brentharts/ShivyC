"""Transpile-ready token kind registry."""

from __future__ import annotations

from shivyc.transpile.tokens import TokenKind

symbol_kinds: list[TokenKind] | None = None
keyword_kinds: list[TokenKind] | None = None

bool_kw: TokenKind | None = None
char_kw: TokenKind | None = None
short_kw: TokenKind | None = None
int_kw: TokenKind | None = None
long_kw: TokenKind | None = None
float_kw: TokenKind | None = None
double_kw: TokenKind | None = None
signed_kw: TokenKind | None = None
unsigned_kw: TokenKind | None = None
void_kw: TokenKind | None = None
return_kw: TokenKind | None = None
if_kw: TokenKind | None = None
else_kw: TokenKind | None = None
while_kw: TokenKind | None = None
do_kw: TokenKind | None = None
switch_kw: TokenKind | None = None
case_kw: TokenKind | None = None
default_kw: TokenKind | None = None
goto_kw: TokenKind | None = None
for_kw: TokenKind | None = None
break_kw: TokenKind | None = None
continue_kw: TokenKind | None = None
auto_kw: TokenKind | None = None
register_kw: TokenKind | None = None
static_kw: TokenKind | None = None
extern_kw: TokenKind | None = None
struct_kw: TokenKind | None = None
union_kw: TokenKind | None = None
enum_kw: TokenKind | None = None
const_kw: TokenKind | None = None
volatile_kw: TokenKind | None = None
restrict_kw: TokenKind | None = None
atomic_kw: TokenKind | None = None
typedef_kw: TokenKind | None = None
sizeof_kw: TokenKind | None = None
alignof_kw: TokenKind | None = None
asm_kw: TokenKind | None = None

incr: TokenKind | None = None
decr: TokenKind | None = None
plusequals: TokenKind | None = None
minusequals: TokenKind | None = None
starequals: TokenKind | None = None
divequals: TokenKind | None = None
modequals: TokenKind | None = None
orequals: TokenKind | None = None
andequals: TokenKind | None = None
xorequals: TokenKind | None = None
lshiftequals: TokenKind | None = None
rshiftequals: TokenKind | None = None
twoequals: TokenKind | None = None
notequal: TokenKind | None = None
bool_and: TokenKind | None = None
bool_or: TokenKind | None = None
lbitshift: TokenKind | None = None
rbitshift: TokenKind | None = None
ltoe: TokenKind | None = None
gtoe: TokenKind | None = None
lt: TokenKind | None = None
gt: TokenKind | None = None
plus: TokenKind | None = None
minus: TokenKind | None = None
slash: TokenKind | None = None
mod: TokenKind | None = None
bool_not: TokenKind | None = None
compl: TokenKind | None = None
bitor: TokenKind | None = None
bitxor: TokenKind | None = None
amp: TokenKind | None = None
question: TokenKind | None = None
colon: TokenKind | None = None
dot: TokenKind | None = None
arrow: TokenKind | None = None

star: TokenKind | None = None
open_paren: TokenKind | None = None
close_paren: TokenKind | None = None
open_brack: TokenKind | None = None
close_brack: TokenKind | None = None
open_sq_brack: TokenKind | None = None
close_sq_brack: TokenKind | None = None
comma: TokenKind | None = None
equals: TokenKind | None = None
dots: TokenKind | None = None
dquote: TokenKind | None = None
squote: TokenKind | None = None
pound: TokenKind | None = None
identifier: TokenKind | None = None
string: TokenKind | None = None
char_string: TokenKind | None = None
include_file: TokenKind | None = None
number: TokenKind | None = None
unrecognized: TokenKind | None = None
semicolon: TokenKind | None = None


def _new_kind_list() -> list[TokenKind]:
    result: list[TokenKind] = []
    return result


def _register(kinds: list[TokenKind], text_repr: str) -> TokenKind:
    kind: TokenKind = TokenKind(text_repr)
    kinds.append(kind)
    return kind


def _sort_kinds_desc(kinds: list[TokenKind]) -> None:
    i: int = 0
    while i < len(kinds):
        j: int = i + 1
        while j < len(kinds):
            if len(kinds[j].text_repr) > len(kinds[i].text_repr):
                tmp: TokenKind = kinds[i]
                kinds[i] = kinds[j]
                kinds[j] = tmp
            j = j + 1
        i = i + 1


def init_token_kinds() -> None:
    """Register all token kinds (call once at startup)."""
    global symbol_kinds, keyword_kinds
    global bool_kw, char_kw, short_kw, int_kw, long_kw, float_kw, double_kw
    global signed_kw, unsigned_kw, void_kw
    global return_kw, if_kw, else_kw, while_kw, do_kw, switch_kw, case_kw
    global default_kw, goto_kw, for_kw, break_kw, continue_kw
    global auto_kw, register_kw, static_kw, extern_kw
    global struct_kw, union_kw, enum_kw, const_kw, volatile_kw, restrict_kw
    global atomic_kw, typedef_kw, sizeof_kw, alignof_kw, asm_kw
    global incr, decr, plusequals, minusequals, starequals, divequals, modequals
    global orequals, andequals, xorequals, lshiftequals, rshiftequals
    global twoequals, notequal, bool_and, bool_or, lbitshift, rbitshift
    global ltoe, gtoe, lt, gt, plus, minus, slash, mod, bool_not, compl
    global bitor, bitxor, amp, question, colon, dot, arrow
    global star, open_paren, close_paren, open_brack, close_brack
    global open_sq_brack, close_sq_brack, comma, equals, dots
    global dquote, squote, pound, identifier, string, char_string
    global include_file, number, unrecognized, semicolon

    symbol_kinds = _new_kind_list()
    keyword_kinds = _new_kind_list()

    bool_kw = _register(keyword_kinds, "_Bool")
    char_kw = _register(keyword_kinds, "char")
    short_kw = _register(keyword_kinds, "short")
    int_kw = _register(keyword_kinds, "int")
    long_kw = _register(keyword_kinds, "long")
    float_kw = _register(keyword_kinds, "float")
    double_kw = _register(keyword_kinds, "double")
    signed_kw = _register(keyword_kinds, "signed")
    unsigned_kw = _register(keyword_kinds, "unsigned")
    void_kw = _register(keyword_kinds, "void")
    return_kw = _register(keyword_kinds, "return")
    if_kw = _register(keyword_kinds, "if")
    else_kw = _register(keyword_kinds, "else")
    while_kw = _register(keyword_kinds, "while")
    do_kw = _register(keyword_kinds, "do")
    switch_kw = _register(keyword_kinds, "switch")
    case_kw = _register(keyword_kinds, "case")
    default_kw = _register(keyword_kinds, "default")
    goto_kw = _register(keyword_kinds, "goto")
    for_kw = _register(keyword_kinds, "for")
    break_kw = _register(keyword_kinds, "break")
    continue_kw = _register(keyword_kinds, "continue")
    auto_kw = _register(keyword_kinds, "auto")
    register_kw = _register(keyword_kinds, "register")
    static_kw = _register(keyword_kinds, "static")
    extern_kw = _register(keyword_kinds, "extern")
    struct_kw = _register(keyword_kinds, "struct")
    union_kw = _register(keyword_kinds, "union")
    enum_kw = _register(keyword_kinds, "enum")
    const_kw = _register(keyword_kinds, "const")
    volatile_kw = _register(keyword_kinds, "volatile")
    restrict_kw = _register(keyword_kinds, "restrict")
    atomic_kw = _register(keyword_kinds, "_Atomic")
    typedef_kw = _register(keyword_kinds, "typedef")
    sizeof_kw = _register(keyword_kinds, "sizeof")
    alignof_kw = _register(keyword_kinds, "_Alignof")
    asm_kw = _register(keyword_kinds, "asm")

    incr = _register(symbol_kinds, "++")
    decr = _register(symbol_kinds, "--")
    plusequals = _register(symbol_kinds, "+=")
    minusequals = _register(symbol_kinds, "-=")
    starequals = _register(symbol_kinds, "*=")
    divequals = _register(symbol_kinds, "/=")
    modequals = _register(symbol_kinds, "%=")
    orequals = _register(symbol_kinds, "|=")
    andequals = _register(symbol_kinds, "&=")
    xorequals = _register(symbol_kinds, "^=")
    lshiftequals = _register(symbol_kinds, "<<=")
    rshiftequals = _register(symbol_kinds, ">>=")
    twoequals = _register(symbol_kinds, "==")
    notequal = _register(symbol_kinds, "!=")
    bool_and = _register(symbol_kinds, "&&")
    bool_or = _register(symbol_kinds, "||")
    lbitshift = _register(symbol_kinds, "<<")
    rbitshift = _register(symbol_kinds, ">>")
    ltoe = _register(symbol_kinds, "<=")
    gtoe = _register(symbol_kinds, ">=")
    lt = _register(symbol_kinds, "<")
    gt = _register(symbol_kinds, ">")
    arrow = _register(symbol_kinds, "->")
    dots = _register(symbol_kinds, "...")
    plus = _register(symbol_kinds, "+")
    minus = _register(symbol_kinds, "-")
    star = _register(symbol_kinds, "*")
    slash = _register(symbol_kinds, "/")
    mod = _register(symbol_kinds, "%")
    equals = _register(symbol_kinds, "=")
    bool_not = _register(symbol_kinds, "!")
    amp = _register(symbol_kinds, "&")
    bitor = _register(symbol_kinds, "|")
    bitxor = _register(symbol_kinds, "^")
    pound = _register(symbol_kinds, "#")
    compl = _register(symbol_kinds, "~")
    dquote = _register(symbol_kinds, '"')
    squote = _register(symbol_kinds, "'")
    open_paren = _register(symbol_kinds, "(")
    close_paren = _register(symbol_kinds, ")")
    open_brack = _register(symbol_kinds, "{")
    close_brack = _register(symbol_kinds, "}")
    open_sq_brack = _register(symbol_kinds, "[")
    close_sq_brack = _register(symbol_kinds, "]")
    comma = _register(symbol_kinds, ",")
    semicolon = _register(symbol_kinds, ";")
    question = _register(symbol_kinds, "?")
    colon = _register(symbol_kinds, ":")
    dot = _register(symbol_kinds, ".")

    identifier = TokenKind("")
    number = TokenKind("")
    unrecognized = TokenKind("")
    string = TokenKind("")
    char_string = TokenKind("")
    include_file = TokenKind("")

    _sort_kinds_desc(symbol_kinds)
    _sort_kinds_desc(keyword_kinds)
