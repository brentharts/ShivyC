"""minidom -- a tiny DOM exposed to page scripts, written in the minipy subset.

Page `<script type="python">` blocks run on minipy (the RPython Python
interpreter), not on the renderer. This prelude is prepended to every page
script and gives it the browser-side globals a script expects -- `document`,
`console`, `window` -- implemented in ordinary (minipy-subset) Python on top of
a flat element list. The browser fills that list with one
`document._register(...)` call per element that carries an `id`, generated from
the parsed DOM (see pyscript_test.py / the runtime glue).

Kept deliberately minimal and within what native minipy supports today: single
inheritance-free classes, methods with `self`, lists, `for`/`in`, string
concatenation, `hasattr`. No `*args`, f-strings, or `__repr__` dispatch (minipy
prints instances as "<Class object>", so DOM objects are formatted explicitly
through `_dom_str`). As real DOM mutation lands it will move behind native host
primitives; today the surface is read + log + alert, which is what the first
`<script type="python">` tests exercise.
"""


class Element:
    def __init__(self, elid, tag, text):
        self.id = elid
        self.tagName = tag
        self.textContent = text

    def getAttribute(self, name):
        if name == "id":
            return self.id
        return ""

    def _dom_str(self):
        if self.id != "":
            return ("<" + self.tagName + " id=\"" + self.id + "\">"
                    + self.textContent + "</" + self.tagName + ">")
        return "<" + self.tagName + ">" + self.textContent + "</" + self.tagName + ">"


class Document:
    def __init__(self):
        self.elements = []

    def _register(self, el):
        self.elements.append(el)

    def getElementById(self, elid):
        for el in self.elements:
            if el.id == elid:
                return el
        return None

    def _dom_str(self):
        return ("[object HTMLDocument] with " + str(len(self.elements))
                + " indexed elements")


def _fmt(x):
    # minipy prints instances as "<Class object>"; format DOM objects (which
    # carry _dom_str) explicitly, and fall back to str() for plain values.
    if hasattr(x, "_dom_str"):
        return x._dom_str()
    return str(x)


class Console:
    def log(self, x):
        print(_fmt(x))


class Window:
    def alert(self, msg):
        print("[alert] " + _fmt(msg))


document = Document()
console = Console()
window = Window()
