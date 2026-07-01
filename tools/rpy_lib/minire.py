# minire: a tiny regular-expression engine covering exactly the subset of the
# `re` module that py2c.py relies on. It is deliberately NOT a general regex
# implementation -- py2c only ever uses anchored `re.match` (plus a couple of
# `re.search`) over patterns built from:
#
#   literals, `.`, the escapes \w \d \s (and \W \D \S), character classes
#   [...] / [^...] with ranges and those same escapes, the quantifiers
#   * + ? and their lazy forms *? +? ??, the anchors ^ and $, escaped
#   metacharacters (\( \) \[ \] \" \\ ...), and capturing groups ( ... ).
#
# There is no alternation (|), no back-references, and no quantified groups,
# because py2c never uses them. Group captures and lazy/greedy backtracking are
# matched to CPython's `re` semantics (verified differentially in the tests).
#
# Written in the minipy subset (classes, while/for, recursion, lists, string
# indexing -- no comprehensions, lambdas, or imports) so the same source runs on
# CPython and on minipy.

# ---- atom kinds -----------------------------------------------------------
# Each compiled atom is a list: [kind, payload, qmin, qmax, greedy]
#   kind:    "lit" "any" "cls" "gs" "ge" "bol" "eol"
#   payload: the char (lit), the class spec (cls), or the group number (gs/ge)
#   qmin/qmax: repetition bounds; qmax == -1 means unbounded. Only consuming
#              atoms (lit/any/cls) are ever quantified; the rest are 1..1.
#   greedy:  1 greedy, 0 lazy
#
# A class spec is [negated, items] where each item is one of:
#   ["r", lo, hi]   inclusive character range
#   ["s", ch]       a shorthand: 'w' 'd' 's' 'W' 'D' 'S'
#   ["c", ch]       a single literal character


def _is_word(ch):
    return (("a" <= ch and ch <= "z") or ("A" <= ch and ch <= "Z")
            or ("0" <= ch and ch <= "9") or ch == "_")


def _is_digit(ch):
    return "0" <= ch and ch <= "9"


def _is_space(ch):
    return ch == " " or ch == "\t" or ch == "\n" or ch == "\r" or ch == "\f" or ch == "\v"


def _shorthand_match(kind, ch):
    if kind == "w":
        return _is_word(ch)
    if kind == "d":
        return _is_digit(ch)
    if kind == "s":
        return _is_space(ch)
    if kind == "W":
        return not _is_word(ch)
    if kind == "D":
        return not _is_digit(ch)
    if kind == "S":
        return not _is_space(ch)
    return False


def _class_match(spec, ch):
    negated = spec[0]
    items = spec[1]
    hit = 0
    i = 0
    while i < len(items):
        it = items[i]
        k = it[0]
        if k == "r":
            if it[1] <= ch and ch <= it[2]:
                hit = 1
        elif k == "s":
            if _shorthand_match(it[1], ch):
                hit = 1
        elif k == "c":
            if it[1] == ch:
                hit = 1
        if hit == 1:
            break
        i = i + 1
    if negated == 1:
        if hit == 1:
            return 0
        return 1
    return hit


class _Compiled:
    def __init__(self, atoms, ngroups):
        self.atoms = atoms
        self.ngroups = ngroups


def _compile_class(pat, i):
    # pat[i] is the char just after '['. Returns (spec, next_index_after_']').
    negated = 0
    if i < len(pat) and pat[i] == "^":
        negated = 1
        i = i + 1
    items = []
    while i < len(pat) and pat[i] != "]":
        c = pat[i]
        if c == "\\":
            nxt = pat[i + 1]
            if nxt == "w" or nxt == "d" or nxt == "s" or nxt == "W" or nxt == "D" or nxt == "S":
                items.append(["s", nxt])
            else:
                items.append(["c", nxt])
            i = i + 2
        else:
            # a range like a-z (only when '-' is between two plain chars)
            if i + 2 < len(pat) and pat[i + 1] == "-" and pat[i + 2] != "]":
                items.append(["r", c, pat[i + 2]])
                i = i + 3
            else:
                items.append(["c", c])
                i = i + 1
    return [negated, items], i + 1        # skip ']'


def _compile(pat):
    atoms = []
    ngroups = 0
    gstack = []
    i = 0
    n = len(pat)
    while i < n:
        c = pat[i]
        atom = None
        if c == "(":
            ngroups = ngroups + 1
            gstack.append(ngroups)
            atoms.append(["gs", ngroups, 1, 1, 1])
            i = i + 1
            continue
        elif c == ")":
            g = gstack[len(gstack) - 1]
            gstack = gstack[0:len(gstack) - 1]
            atoms.append(["ge", g, 1, 1, 1])
            i = i + 1
            continue
        elif c == "^":
            atoms.append(["bol", 0, 1, 1, 1])
            i = i + 1
            continue
        elif c == "$":
            atoms.append(["eol", 0, 1, 1, 1])
            i = i + 1
            continue
        elif c == ".":
            atom = ["any", 0, 1, 1, 1]
            i = i + 1
        elif c == "[":
            spec, i = _compile_class(pat, i + 1)
            atom = ["cls", spec, 1, 1, 1]
        elif c == "\\":
            nxt = pat[i + 1]
            if nxt == "w" or nxt == "d" or nxt == "s" or nxt == "W" or nxt == "D" or nxt == "S":
                atom = ["cls", [0, [["s", nxt]]], 1, 1, 1]
            else:
                atom = ["lit", nxt, 1, 1, 1]
            i = i + 2
        else:
            atom = ["lit", c, 1, 1, 1]
            i = i + 1
        # optional quantifier on the atom just built
        if i < n and (pat[i] == "*" or pat[i] == "+" or pat[i] == "?"):
            q = pat[i]
            i = i + 1
            if q == "*":
                atom[2] = 0
                atom[3] = -1
            elif q == "+":
                atom[2] = 1
                atom[3] = -1
            else:
                atom[2] = 0
                atom[3] = 1
            if i < n and pat[i] == "?":
                atom[4] = 0
                i = i + 1
        atoms.append(atom)
    return _Compiled(atoms, ngroups)


def _atom_at(atom, s, si):
    # Does the consuming atom match the single char at s[si]?
    if si >= len(s):
        return 0
    kind = atom[0]
    if kind == "any":
        return 1
    if kind == "lit":
        if s[si] == atom[1]:
            return 1
        return 0
    if kind == "cls":
        return _class_match(atom[1], s[si])
    return 0


def _m(atoms, ai, s, si, caps):
    # Match atoms[ai:] against s starting at si. Returns the end index on
    # success or -1. `caps` is a flat list: caps[2*g] / caps[2*g+1] are the
    # start/end of group g (updated in place; overwritten on retry).
    if ai >= len(atoms):
        return si
    atom = atoms[ai]
    kind = atom[0]
    if kind == "bol":
        if si == 0:
            return _m(atoms, ai + 1, s, si, caps)
        return -1
    if kind == "eol":
        if si == len(s):
            return _m(atoms, ai + 1, s, si, caps)
        return -1
    if kind == "gs":
        caps[2 * atom[1]] = si
        return _m(atoms, ai + 1, s, si, caps)
    if kind == "ge":
        caps[2 * atom[1] + 1] = si
        return _m(atoms, ai + 1, s, si, caps)
    # consuming atom, possibly quantified
    qmin = atom[2]
    qmax = atom[3]
    greedy = atom[4]
    # how many consecutive copies can match from si
    hi = 0
    pos = si
    while (qmax < 0 or hi < qmax) and _atom_at(atom, s, pos) == 1:
        pos = pos + 1
        hi = hi + 1
    if greedy == 1:
        k = hi
        while k >= qmin:
            r = _m(atoms, ai + 1, s, si + k, caps)
            if r >= 0:
                return r
            k = k - 1
        return -1
    else:
        k = qmin
        while k <= hi:
            r = _m(atoms, ai + 1, s, si + k, caps)
            if r >= 0:
                return r
            k = k + 1
        return -1


class Match:
    def __init__(self, s, caps):
        self.string = s
        self.caps = caps            # caps[0],caps[1] = whole match span

    def group(self, n=0):
        a = self.caps[2 * n]
        b = self.caps[2 * n + 1]
        if a < 0 or b < 0:
            return None
        return self.string[a:b]

    def start(self, n=0):
        return self.caps[2 * n]

    def end(self, n=0):
        return self.caps[2 * n + 1]


def _run(comp, s, at):
    caps = []
    total = (comp.ngroups + 1) * 2
    i = 0
    while i < total:
        caps.append(-1)
        i = i + 1
    caps[0] = at
    end = _m(comp.atoms, 0, s, at, caps)
    if end < 0:
        return None
    caps[1] = end
    return Match(s, caps)


class Pattern:
    def __init__(self, comp):
        self.comp = comp

    def match(self, s):
        return _run(self.comp, s, 0)

    def search(self, s):
        i = 0
        while i <= len(s):
            m = _run(self.comp, s, i)
            if m is not None:
                return m
            i = i + 1
        return None


def compile(pat):
    return Pattern(_compile(pat))


def match(pat, s):
    return _run(_compile(pat), s, 0)


def search(pat, s):
    return compile(pat).search(s)
