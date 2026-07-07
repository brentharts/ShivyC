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

    def _serialize(self):
        b = self.body
        if b == None:
            return "{\"type\":\"body\",\"attributes\":{},\"text\":\"\",\"children\":[]}"
        return _ser_node(b)

    def _dom_str(self):
        return "[object HTMLDocument] with " + str(len(self._all)) + " nodes"


def _ser_node(e):
    s = "{\"type\":" + _jstr(e.tagName) + ",\"attributes\":{"
    first = 1
    if e.eid != "":
        s = s + "\"id\":" + _jstr(e.eid)
        first = 0
    if e.value != "":
        if first == 0:
            s = s + ","
        s = s + "\"value\":" + _jstr(e.value)
        first = 0
    if e.onclick != None:
        if first == 0:
            s = s + ","
        s = s + "\"onclick\":" + _jstr(str(e._handle))
        first = 0
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


def __console():
    return console._text()


def __alert():
    return window._text()
