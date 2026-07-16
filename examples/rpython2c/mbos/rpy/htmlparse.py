"""htmlparse -- HTML -> dom.Node tree, in pure rpython.

The in-kernel counterpart of what www2json.py does on the host for the Wayland
minibrowser, and the rpython replacement for mbos's hand-written html.c: a tiny
tag/text tokenizer that builds the same {tag, text, href, children} Node tree
the renderer walks. Modelled on minijson.py's Parser (same subset, same
conventions: ord() comparisons, substring slices for runs, a boxed obj stack).

Scope matches html.c: open/close tags, void tags, the href attribute, text runs
with whitespace collapsed. Unknown tags stay as generic containers.
"""

from dom import Node


def _lower(s: "char*") -> "char*":
    out = ""
    i = 0
    n = len(s)
    while i < n:
        c = ord(s[i])
        if c >= 65 and c <= 90:
            out = out + chr(c + 32)
        else:
            out = out + s[i]
        i = i + 1
    return out


def _is_void(t: "char*") -> int:
    if t == "br" or t == "hr" or t == "img":
        return 1
    if t == "input" or t == "meta" or t == "link":
        return 1
    return 0


class HtmlParser:
    def __init__(self, s: "char*"):
        self.s = s
        self.pos = 0
        self.n = len(s)

    def _ws(self, c: int) -> int:
        if c == 32 or c == 9 or c == 10 or c == 13:
            return 1
        return 0

    def skip_to_gt(self) -> None:
        while self.pos < self.n:
            if ord(self.s[self.pos]) == 62:      # '>'
                self.pos = self.pos + 1
                return
            self.pos = self.pos + 1

    def read_name(self) -> "char*":
        start = self.pos
        while self.pos < self.n:
            c = ord(self.s[self.pos])
            if c == 62 or c == 47 or self._ws(c) == 1:   # '>' '/' ws
                break
            self.pos = self.pos + 1
        return _lower(self.s[start:self.pos])

    def read_attrs(self) -> "char*":
        # scan up to '>'; returns the href value ("" if none)
        href = ""
        while self.pos < self.n:
            c = ord(self.s[self.pos])
            if c == 62:                                   # '>'
                self.pos = self.pos + 1
                return href
            if self._ws(c) == 1 or c == 47:               # ws or '/'
                self.pos = self.pos + 1
                continue
            astart = self.pos
            while self.pos < self.n:
                c = ord(self.s[self.pos])
                if c == 61 or c == 62 or self._ws(c) == 1:   # '=' '>' ws
                    break
                self.pos = self.pos + 1
            aname = _lower(self.s[astart:self.pos])
            val = ""
            if self.pos < self.n and ord(self.s[self.pos]) == 61:   # '='
                self.pos = self.pos + 1
                q = 0
                if self.pos < self.n:
                    c = ord(self.s[self.pos])
                    if c == 34 or c == 39:                # '"' or '\''
                        q = c
                        self.pos = self.pos + 1
                vstart = self.pos
                if q != 0:
                    while self.pos < self.n and ord(self.s[self.pos]) != q:
                        self.pos = self.pos + 1
                    val = self.s[vstart:self.pos]
                    if self.pos < self.n:
                        self.pos = self.pos + 1          # closing quote
                else:
                    while self.pos < self.n:
                        c = ord(self.s[self.pos])
                        if self._ws(c) == 1 or c == 62:
                            break
                        self.pos = self.pos + 1
                    val = self.s[vstart:self.pos]
            if aname == "href":
                href = val
        return href

    def read_text(self) -> "char*":
        # text run up to '<', whitespace collapsed, trimmed
        out = ""
        prev_space = 1
        while self.pos < self.n:
            c = ord(self.s[self.pos])
            if c == 60:                                   # '<'
                break
            if self._ws(c) == 1:
                if prev_space == 0:
                    out = out + " "
                    prev_space = 1
            else:
                out = out + self.s[self.pos]
                prev_space = 0
            self.pos = self.pos + 1
        # trim one trailing space
        m = len(out)
        if m > 0 and ord(out[m - 1]) == 32:
            out = out[0:m - 1]
        return out


def _mk(tag: "char*") -> "obj":
    # Return a fresh Node as `obj`, exactly like minijson.parse_node returns an
    # obj. This is the boxing point: py2c passes a concrete `Node` local by raw
    # pointer, but Node.add / list.append expect an `obj` by value -- crossing
    # that boundary with an obj-typed local (the function's return type) is what
    # makes the generated call pass the full boxed value, not a bare pointer.
    n = Node(tag)
    return n


def parse_html(s: "char*") -> "obj":
    p = HtmlParser(s)
    doc = _mk("document")
    stack: "list[obj]" = []
    stack.append(doc)

    while p.pos < p.n:
        c = ord(p.s[p.pos])
        if c == 60:                                       # '<'
            if p.pos + 1 < p.n and ord(p.s[p.pos + 1]) == 33:   # '<!' comment/doctype
                p.skip_to_gt()
                continue
            if p.pos + 1 < p.n and ord(p.s[p.pos + 1]) == 47:   # '</' close
                p.pos = p.pos + 2
                name = p.read_name()
                p.skip_to_gt()
                if len(stack) > 1:
                    top = stack[len(stack) - 1]
                    if top.tag() == name:
                        stack.pop()
                continue
            # open tag
            p.pos = p.pos + 1
            name = p.read_name()
            el = _mk(name)
            el.href = p.read_attrs()
            top = stack[len(stack) - 1]
            top.add(el)
            if _is_void(name) == 0:
                stack.append(el)
            continue
        # text run
        txt = p.read_text()
        if len(txt) > 0:
            t = _mk("text")
            t.text = txt
            top = stack[len(stack) - 1]
            top.add(t)
    return doc
