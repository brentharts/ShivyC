"""A C-subset lexer kernel written in rpython -- a first hot compiler component
rewritten for native speed.

Lexing touches every source character, so it is the classic compiler hotspot.
This kernel scans a C source string and folds every token (its kind, length and
a byte-hash of its text) into a rolling checksum. The same file:

  * run as plain Python  -> the reference implementation, and
  * transpiled by tools/py2c.py -> the identical logic as native C.

Because `ord(s[i])` on a char* compiles to a direct byte read, and the keyword
and symbol tables are typed `list[int]` (unboxed, not the tagged object model),
the inner loop has no per-character allocation -- it is the C a human would
write, generated from Python.

All arithmetic is kept within 31 bits so Python's big integers and C's 64-bit
`long` agree exactly: the checksum from the reference run and the transpiled
binary is identical, byte for byte.

    python3 examples/rpython2c/compiler/lexer_kernel.py        # reference
    python3 -m shivyc.main --no-cache lexer_kernel.py -o /tmp/lk && /tmp/lk
"""

HMOD = 2147483647        # 2**31 - 1; keeps every intermediate inside int64


def is_alpha(c: "int") -> int:
    return 1 if (c >= 65 and c <= 90) or (c >= 97 and c <= 122) or c == 95 else 0


def is_digit(c: "int") -> int:
    return 1 if c >= 48 and c <= 57 else 0


def is_alnum(c: "int") -> int:
    return 1 if is_alpha(c) == 1 or is_digit(c) == 1 else 0


def is_space(c: "int") -> int:
    return 1 if c == 32 or c == 9 or c == 10 or c == 13 else 0


def in_list(xs: "list[int]", v: "i64") -> int:
    n = len(xs)
    i = 0
    while i < n:
        if xs[i] == v:
            return 1
        i = i + 1
    return 0


def single_syms() -> "list[int]":
    # + - * / % = ! < > & | ^ # ~ " ' ( ) { } [ ] , ; ? : .
    xs: "list[int]" = []
    xs.append(43)
    xs.append(45)
    xs.append(42)
    xs.append(47)
    xs.append(37)
    xs.append(61)
    xs.append(33)
    xs.append(60)
    xs.append(62)
    xs.append(38)
    xs.append(124)
    xs.append(94)
    xs.append(35)
    xs.append(126)
    xs.append(34)
    xs.append(39)
    xs.append(40)
    xs.append(41)
    xs.append(123)
    xs.append(125)
    xs.append(91)
    xs.append(93)
    xs.append(44)
    xs.append(59)
    xs.append(63)
    xs.append(58)
    xs.append(46)
    return xs


def eq_lhs() -> "list[int]":
    # chars c for which `c=` is a compound operator: + - * / % | & ^ = ! < >
    xs: "list[int]" = []
    xs.append(43)
    xs.append(45)
    xs.append(42)
    xs.append(47)
    xs.append(37)
    xs.append(124)
    xs.append(38)
    xs.append(94)
    xs.append(61)
    xs.append(33)
    xs.append(60)
    xs.append(62)
    return xs


def match_symbol(s: "char*", i: "int", L: "int",
                 singles: "list[int]", eqlhs: "list[int]") -> int:
    """Greedy operator/punctuation match: returns matched length 1..3, else 0."""
    c0 = ord(s[i])
    c1 = ord(s[i + 1]) if i + 1 < L else 0
    c2 = ord(s[i + 2]) if i + 2 < L else 0
    # 3-char: <<=  >>=  ...
    if c0 == 60 and c1 == 60 and c2 == 61:
        return 3
    if c0 == 62 and c1 == 62 and c2 == 61:
        return 3
    if c0 == 46 and c1 == 46 and c2 == 46:
        return 3
    # 2-char compound operators
    if c1 == 61 and in_list(eqlhs, c0) == 1:        # += -= ... == != <= >=
        return 2
    if c0 == 43 and c1 == 43:                       # ++
        return 2
    if c0 == 45 and c1 == 45:                       # --
        return 2
    if c0 == 38 and c1 == 38:                       # &&
        return 2
    if c0 == 124 and c1 == 124:                     # ||
        return 2
    if c0 == 60 and c1 == 60:                       # <<
        return 2
    if c0 == 62 and c1 == 62:                       # >>
        return 2
    if c0 == 45 and c1 == 62:                       # ->
        return 2
    if in_list(singles, c0) == 1:                   # single-char symbol
        return 1
    return 0


def word_hash(s: "char*", start: "int", n: "int") -> "i64":
    h: "i64" = 2166136261 % HMOD
    j = 0
    while j < n:
        h = (h * 131 + ord(s[start + j])) % HMOD
        j = j + 1
    return h


def keyword_hashes() -> "list[int]":
    kw: "list[int]" = []
    names = "_Bool|char|short|int|long|float|double|signed|unsigned|void|" + \
            "return|if|else|while|do|switch|case|default|goto|for|break|" + \
            "continue|sizeof|typedef|static|extern|struct|union|const|" + \
            "volatile|register|auto|enum|restrict|asm"
    L = len(names)
    start = 0
    i = 0
    while i <= L:
        if i == L or ord(names[i]) == 124:          # '|' separator
            if i > start:
                kw.append(word_hash(names, start, i - start))
            start = i + 1
        i = i + 1
    return kw


def tokenize_checksum(s: "char*") -> "i64":
    kw = keyword_hashes()
    singles = single_syms()
    eqlhs = eq_lhs()
    L = len(s)
    i = 0
    acc: "i64" = 1469598103 % HMOD
    ntok = 0
    while i < L:
        c = ord(s[i])
        if is_space(c) == 1:                         # whitespace
            i = i + 1
            continue
        if c == 47 and i + 1 < L and ord(s[i + 1]) == 47:   # // line comment
            i = i + 2
            while i < L and ord(s[i]) != 10:
                i = i + 1
            continue
        if c == 47 and i + 1 < L and ord(s[i + 1]) == 42:   # /* block comment */
            i = i + 2
            while i + 1 < L and not (ord(s[i]) == 42 and ord(s[i + 1]) == 47):
                i = i + 1
            i = i + 2
            continue
        kind = 0
        start = i
        if is_alpha(c) == 1:                          # identifier or keyword
            while i < L and is_alnum(ord(s[i])) == 1:
                i = i + 1
            h: "i64" = word_hash(s, start, i - start)
            kind = 2 if in_list(kw, h) == 1 else 1
        elif is_digit(c) == 1:                         # number
            while i < L and (is_alnum(ord(s[i])) == 1 or ord(s[i]) == 46):
                i = i + 1
            kind = 3
        elif c == 34:                                  # "string"
            i = i + 1
            while i < L and ord(s[i]) != 34:
                if ord(s[i]) == 92:
                    i = i + 1
                i = i + 1
            i = i + 1
            kind = 4
        elif c == 39:                                  # 'c'
            i = i + 1
            while i < L and ord(s[i]) != 39:
                if ord(s[i]) == 92:
                    i = i + 1
                i = i + 1
            i = i + 1
            kind = 5
        else:
            m = match_symbol(s, i, L, singles, eqlhs)
            if m == 0:
                i = i + 1
                continue
            i = i + m
            kind = 6
        th: "i64" = word_hash(s, start, i - start)
        acc = (acc * 131 + kind) % HMOD
        acc = (acc * 131 + th) % HMOD
        acc = (acc * 131 + (i - start)) % HMOD
        ntok = ntok + 1
    return (acc * 131 + ntok) % HMOD


def sample_source() -> "char*":
    return "int fib(int n){if(n<2)return n;return fib(n-1)+fib(n-2);}" + \
           "/* sum */ double s=0.0; for(int i=0;i<=100;i++){s+=i*1.5;}" + \
           "char* p=\"hello\\n\"; if(p!=0 && *p=='h'){p->x>>=2;} // done\n"


def run(reps: "int") -> "i64":
    src = sample_source()
    acc: "i64" = 0
    r = 0
    while r < reps:
        acc = (acc * 131 + tokenize_checksum(src)) % HMOD
        r = r + 1
    return acc


def main() -> int:
    return run(1) % 256
