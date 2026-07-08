"""minijson -- a tiny runtime JSON reader that builds a dom.Node tree.

This is what makes the renderer genuinely "take in a json file" at runtime (and
what live navigation stands on): instead of co-compiling a generated
`page_data.py`, the binary reads a `page.json` bundle from disk and parses it
into the `dom.Node` tree the renderer walks.

It is deliberately *schema-specific*, not a general JSON library. It parses
exactly the shape `www2json.py` emits -- a bundle object with string fields plus
a `dom` node, where every node is `{"type","attributes","text","children"}`,
attribute values are strings, and children is an array of nodes. Restricting the
value space to {object, array, string, skipped-primitive} keeps every parse
method returning a single concrete rpython type (no heterogeneous JSON value),
so the whole thing lowers to direct C with no object-core fallback.

Nodes are built with a concrete `Node` local and direct field writes; children
are attached with `node.add(child_obj)` through the concrete receiver; recursion
returns the boxed `obj` -- the same patterns the renderer and page_data use.
"""
from dom import Node


def read_file(path: "char*") -> "char*":
    """Read an entire text file into one string. Uses a single whole-file read:
    concatenating line by line is O(size^2) in the arena (every intermediate
    string stays allocated), which alone can exhaust it on a large page."""
    f = open(path, "r")
    out = f.read()
    f.close()
    return out


class Parser:
    def __init__(self, s: "char*"):
        self.s = s
        self.pos = 0
        self.n = len(s)

    def at(self) -> int:
        if self.pos < self.n:
            return ord(self.s[self.pos])
        return -1

    def skip_ws(self) -> None:
        while self.pos < self.n:
            c = ord(self.s[self.pos])
            if c == 32 or c == 9 or c == 10 or c == 13:
                self.pos = self.pos + 1
            else:
                return

    def parse_string(self) -> "char*":
        # Assumes the current char is the opening quote.
        self.pos = self.pos + 1
        start = self.pos
        # Fast path: scan to the closing quote. If no escape appears (the common
        # case, and what a page's long text nodes are), the value is one
        # substring -- a single allocation instead of one per character, which
        # in the no-free arena is the difference between O(n) and O(n^2) space.
        while self.pos < self.n:
            c = ord(self.s[self.pos])
            if c == 34:                 # closing quote, no escapes seen
                out = self.s[start:self.pos]
                self.pos = self.pos + 1
                return out
            if c == 92:                 # backslash: switch to the slow path
                break
            self.pos = self.pos + 1
        # Slow path (the string contains escapes): keep the unescaped prefix as
        # one slice, then append the remainder char by char honoring escapes.
        out = self.s[start:self.pos]
        while self.pos < self.n:
            c = ord(self.s[self.pos])
            if c == 34:                 # closing quote
                self.pos = self.pos + 1
                return out
            if c == 92:                 # backslash escape
                self.pos = self.pos + 1
                if self.pos >= self.n:
                    return out
                e = ord(self.s[self.pos])
                if e == 110:
                    out = out + "\n"
                elif e == 116:
                    out = out + "\t"
                elif e == 114:
                    out = out + "\r"
                elif e == 34:
                    out = out + "\""
                elif e == 92:
                    out = out + "\\"
                elif e == 47:
                    out = out + "/"
                elif e == 117:          # \uXXXX -> '?' (the font is ASCII-only)
                    out = out + "?"
                    self.pos = self.pos + 4
                else:
                    out = out + chr(e)
                self.pos = self.pos + 1
            else:
                out = out + chr(c)
                self.pos = self.pos + 1
        return out

    def skip_value(self) -> None:
        # Skip a JSON value we don't model: string, primitive, or a balanced
        # object/array (with strings inside handled so braces in text don't
        # miscount).
        self.skip_ws()
        if self.pos >= self.n:
            return
        c = ord(self.s[self.pos])
        if c == 34:
            self.parse_string()
            return
        if c == 123 or c == 91:         # { or [
            depth = 0
            while self.pos < self.n:
                cc = ord(self.s[self.pos])
                if cc == 34:
                    self.parse_string()
                elif cc == 123 or cc == 91:
                    depth = depth + 1
                    self.pos = self.pos + 1
                elif cc == 125 or cc == 93:
                    depth = depth - 1
                    self.pos = self.pos + 1
                    if depth == 0:
                        return
                else:
                    self.pos = self.pos + 1
            return
        # primitive: run to the next separator
        while self.pos < self.n:
            cc = ord(self.s[self.pos])
            if cc == 44 or cc == 125 or cc == 93:
                return
            if cc == 32 or cc == 9 or cc == 10 or cc == 13:
                return
            self.pos = self.pos + 1

    def parse_node(self) -> "obj":
        # Everything that touches the Node is inlined here so the concrete local
        # `node` never crosses a method boundary as a typed param (a cross-module
        # class annotation would not module-qualify in the generated C).
        node = Node("")
        self.skip_ws()
        if self.pos < self.n and ord(self.s[self.pos]) == 123:
            self.pos = self.pos + 1
        self.skip_ws()
        while self.pos < self.n:
            c = ord(self.s[self.pos])
            if c == 125:                # }
                self.pos = self.pos + 1
                return node
            if c == 34:
                key = self.parse_string()
                self.skip_ws()
                if self.pos < self.n and ord(self.s[self.pos]) == 58:
                    self.pos = self.pos + 1
                self.skip_ws()
                if key == "type":
                    node.tag_name = self.parse_string()
                elif key == "text":
                    node.text = self.parse_string()
                elif key == "attributes":
                    # inline object of "k":"v" string pairs
                    self.skip_ws()
                    if self.pos < self.n and ord(self.s[self.pos]) == 123:
                        self.pos = self.pos + 1
                    self.skip_ws()
                    while self.pos < self.n:
                        ac = ord(self.s[self.pos])
                        if ac == 125:   # }
                            self.pos = self.pos + 1
                            break
                        if ac == 34:
                            akey = self.parse_string()
                            self.skip_ws()
                            if self.pos < self.n and ord(self.s[self.pos]) == 58:
                                self.pos = self.pos + 1
                            self.skip_ws()
                            aval = self.parse_string()
                            if akey == "href":
                                node.href = aval
                            elif akey == "name":
                                node.name = aval
                            elif akey == "value":
                                node.value = aval
                            elif akey == "type":
                                node.itype = aval
                            elif akey == "onclick":
                                node.onclick = aval
                            elif akey == "id":
                                node.eid = aval
                            elif akey == "h":
                                node.shandle = aval
                            elif akey == "placeholder":
                                node.placeholder = aval
                            elif akey == "src":
                                node.src = aval
                            self.skip_ws()
                            if self.pos < self.n and ord(self.s[self.pos]) == 44:
                                self.pos = self.pos + 1
                            self.skip_ws()
                        else:
                            self.pos = self.pos + 1
                elif key == "children":
                    # inline array of node objects
                    self.skip_ws()
                    if self.pos < self.n and ord(self.s[self.pos]) == 91:
                        self.pos = self.pos + 1
                    self.skip_ws()
                    while self.pos < self.n:
                        cc = ord(self.s[self.pos])
                        if cc == 93:    # ]
                            self.pos = self.pos + 1
                            break
                        if cc == 123:   # { -> a child node
                            child = self.parse_node()
                            node.add(child)
                            self.skip_ws()
                            if self.pos < self.n and ord(self.s[self.pos]) == 44:
                                self.pos = self.pos + 1
                            self.skip_ws()
                        else:
                            self.pos = self.pos + 1
                else:
                    self.skip_value()
                self.skip_ws()
                if self.pos < self.n and ord(self.s[self.pos]) == 44:
                    self.pos = self.pos + 1
                self.skip_ws()
            else:
                self.pos = self.pos + 1
        return node

    def parse_bundle(self) -> "obj":
        # Top-level bundle object: return the `dom` node; skip source/title/etc.
        self.skip_ws()
        if self.pos < self.n and ord(self.s[self.pos]) == 123:
            self.pos = self.pos + 1
        self.skip_ws()
        while self.pos < self.n:
            c = ord(self.s[self.pos])
            if c == 125:
                self.pos = self.pos + 1
                return Node("body")
            if c == 34:
                key = self.parse_string()
                self.skip_ws()
                if self.pos < self.n and ord(self.s[self.pos]) == 58:
                    self.pos = self.pos + 1
                self.skip_ws()
                if key == "dom":
                    return self.parse_node()
                self.skip_value()
                self.skip_ws()
                if self.pos < self.n and ord(self.s[self.pos]) == 44:
                    self.pos = self.pos + 1
                self.skip_ws()
            else:
                self.pos = self.pos + 1
        return Node("body")


def load_page(path: "char*") -> "obj":
    """Read a page.json bundle from `path` and return its DOM root Node."""
    s = read_file(path)
    p = Parser(s)
    return p.parse_bundle()


def parse_dom_str(s: "char*") -> "obj":
    """Parse a bare DOM node JSON string (a {type,attributes,text,children}
    object, as the live minidom serializes document.body) into a Node tree."""
    p = Parser(s)
    return p.parse_node()


def _find(s: "char*", sub: "char*", start: "int") -> "int":
    n = len(s)
    m = len(sub)
    i = start
    while i <= n - m:
        j = 0
        while j < m and ord(s[i + j]) == ord(sub[j]):
            j = j + 1
        if j == m:
            return i
        i = i + 1
    return -1


def has_python(path: "char*") -> "int":
    """True if the page.json bundle at `path` carries a non-empty "python" field
    (i.e. the page has a <script type="python">). A pure text scan so it is safe
    to call off-target (CPython), gating whether the browser boots minipy."""
    s = read_file(path)
    n = len(s)
    idx = _find(s, "\"python\"", 0)
    if idx < 0:
        return 0
    i = idx + 8
    while i < n and (ord(s[i]) == 32 or ord(s[i]) == 58 or ord(s[i]) == 9):
        i = i + 1
    if i < n and ord(s[i]) == 34:          # opening quote of the value
        if i + 1 < n and ord(s[i + 1]) == 34:
            return 0                        # "" -> empty
        return 1
    return 0


def has_rpython(path: "char*") -> "int":
    """True if the bundle carries a non-empty "rpython" map (i.e. the page ships
    <script type="rpython"> blocks to JIT-compile). Pure scan, CPython-safe."""
    s = read_file(path)
    n = len(s)
    idx = _find(s, "\"rpython\"", 0)
    if idx < 0:
        return 0
    i = idx + 9
    while i < n and ord(s[i]) != 123:      # advance to '{'
        i = i + 1
    i = i + 1
    while i < n and (ord(s[i]) == 32 or ord(s[i]) == 10 or ord(s[i]) == 9
                     or ord(s[i]) == 13):
        i = i + 1
    if i < n and ord(s[i]) != 125:         # non-empty if next isn't '}'
        return 1
    return 0
