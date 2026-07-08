"""minidom -- the DOM exposed to page scripts, in the minipy subset.

Runs on the embedded minipy interpreter (not the renderer). The script mutates
these ordinary Python objects -- createElement / appendChild / value= / onclick=
-- and the browser re-renders from them: after each handler it asks minipy to
serialize `document.body` to JSON (the same {type,attributes,text,children}
shape minijson already parses) and rebuilds the widget tree. So all DOM logic
lives here in interpreted Python; the browser only ever reads back a string.

minipy-subset constraints that shaped this file:
  * a function stored in an attribute can't be called as `obj.cb()` (that is a
    method lookup) -- bind it to a local first: `h = obj.cb; h()` (see _fire).
  * keep mutable counters/registries as object fields, not module globals.
  * no *args / __repr__ dispatch / f-strings; DOM objects format via _dom_str.

Event handlers reach back through integer handles: every element gets a unique
_handle; an element with an onclick serializes `"onclick": "<handle>"`, and the
browser calls __fire(handle) to run it. Handlers may be set from HTML
(onclick="foo()", wired by pycompile to `el.onclick = foo`) or from script
(`btn.onclick = bar`) -- both are just callables here.
"""


def _jstr(x):
    out = "\""
    i = 0
    n = len(x)
    while i < n:
        c = x[i]
        if c == "\"":
            out = out + "\\\""
        elif c == "\\":
            out = out + "\\\\"
        elif c == "\n":
            out = out + "\\n"
        elif c == "\t":
            out = out + "\\t"
        else:
            out = out + c
        i = i + 1
    return out + "\""


def _join(lst):
    s = ""
    i = 0
    while i < len(lst):
        if i > 0:
            s = s + "\n"
        s = s + lst[i]
        i = i + 1
    return s


class Element:
    def __init__(self, tag, handle):
        self.tagName = tag
        self._handle = handle
        self.textContent = ""
        self.value = ""
        self.eid = ""
        self.onclick = None
        self.children = []
        self.parentNode = None

    def setAttribute(self, name, val):
        if name == "id":
            self.eid = val
        elif name == "value":
            self.value = val
        elif name == "class":
            pass

    def getAttribute(self, name):
        if name == "id":
            return self.eid
        if name == "value":
            return self.value
        return ""

    def appendChild(self, child):
        child.parentNode = self
        self.children.append(child)
        return child

    def _dom_str(self):
        if self.eid != "":
            return "<" + self.tagName + " id=\"" + self.eid + "\">"
        return "<" + self.tagName + ">"


class Document:
    def __init__(self):
        self.body = None
        self._hcounter = 0
        self._all = []

    def _new_handle(self):
        self._hcounter = self._hcounter + 1
        return self._hcounter

    def createElement(self, tag):
        h = self._new_handle()
        e = Element(tag, h)
        self._all.append(e)
        return e

    def getElementById(self, elid):
        for e in self._all:
            if e.eid == elid:
                return e
        return None

    def _by_handle(self, h):
        for e in self._all:
            if e._handle == h:
                return e
        return None

    def _fire(self, h):
        e = self._by_handle(h)
        if e != None:
            cb = e.onclick             # bind first; minipy cannot call obj.attr()
            if cb != None:
                cb()
        return 0

    def _set_value(self, h, v):
        # Two-way binding: the browser pushes an edited input's text back into
        # the DOM by handle, so scripts read the typed value.
        e = self._by_handle(h)
        if e != None:
            e.value = v
        return 0

    def _set_text(self, h, v):
        # Native page code writes an element's text directly, by handle, through
        # the host FFI (mb_dom_set_text -> __set_text). The next render shows it.
        e = self._by_handle(h)
        if e != None:
            e.textContent = v
        return 0

    def _get_value(self, h):
        e = self._by_handle(h)
        if e != None:
            return e.value
        return ""

    def _get_text(self, h):
        e = self._by_handle(h)
        if e != None:
            return e.textContent
        return ""

    def _get_int(self, h):
        # Parse an element's value as an integer (digits only, optional '-'),
        # so native code can read a numeric field without a string round-trip.
        e = self._by_handle(h)
        if e == None:
            return 0
        s = e.value
        n = 0
        i = 0
        neg = 0
        if len(s) > 0 and s[0] == "-":
            neg = 1
            i = 1
        while i < len(s):
            c = ord(s[i])
            if c >= 48 and c <= 57:
                n = n * 10 + (c - 48)
            i = i + 1
        if neg == 1:
            return -n
        return n

    def _remove(self, h):
        # removeChild by handle: detach the element from its parent.
        e = self._by_handle(h)
        if e == None:
            return 0
        p = e.parentNode
        if p != None:
            kids = []
            i = 0
            while i < len(p.children):
                c = p.children[i]
                if c._handle != h:
                    kids.append(c)
                i = i + 1
            p.children = kids
            e.parentNode = None
        return 0

    def _create_child(self, parent_h, tag, text):
        # createElement + set text + append, under a parent by handle; returns
        # the new child's handle so native code can address it further.
        p = self._by_handle(parent_h)
        if p == None:
            return -1
        e = self.createElement(tag)
        e.textContent = text
        p.appendChild(e)
        return e._handle

    def _serialize(self):
        b = self.body
        if b == None:
            return "{\"type\":\"body\",\"attributes\":{},\"text\":\"\",\"children\":[]}"
        return _ser_node(b)

    def _dom_str(self):
        return "[object HTMLDocument] with " + str(len(self._all)) + " nodes"


def _ser_node(e):
    s = "{\"type\":" + _jstr(e.tagName) + ",\"attributes\":{\"h\":" \
        + _jstr(str(e._handle))
    if e.eid != "":
        s = s + ",\"id\":" + _jstr(e.eid)
    if e.value != "":
        s = s + ",\"value\":" + _jstr(e.value)
    if e.onclick != None:
        s = s + ",\"onclick\":" + _jstr(str(e._handle))
    s = s + "},\"text\":" + _jstr(e.textContent) + ",\"children\":["
    i = 0
    n = len(e.children)
    while i < n:
        if i > 0:
            s = s + ","
        ch = e.children[i]
        s = s + _ser_node(ch)
        i = i + 1
    return s + "]}"


def _fmt(x):
    if hasattr(x, "_dom_str"):
        return x._dom_str()
    return str(x)


class Console:
    def __init__(self):
        self.lines = []

    def log(self, x):
        self.lines.append(_fmt(x))
        print(_fmt(x))

    def _text(self):
        return _join(self.lines)


class Window:
    def __init__(self):
        self.alerts = []

    def alert(self, msg):
        self.alerts.append(_fmt(msg))
        print("[alert] " + _fmt(msg))

    def _text(self):
        return _join(self.alerts)


document = Document()
console = Console()
window = Window()
document.body = document.createElement("body")


def __serialize():
    return document._serialize()


def __fire(h):
    return document._fire(h)


def __set_value(h, v):
    return document._set_value(h, v)


def __set_text(h, v):
    return document._set_text(h, v)


def __get_value(h):
    return document._get_value(h)


def __get_text(h):
    return document._get_text(h)


def __get_int(h):
    return document._get_int(h)


def __remove(h):
    return document._remove(h)


def __create_child(parent_h, tag, text):
    return document._create_child(parent_h, tag, text)


def __console():
    return console._text()


def __alert():
    return window._text()
