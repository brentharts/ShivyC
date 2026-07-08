"""dom -- the minibrowser's DOM node model, in pure rpython.

A page is a tree of `Node`s. This mirrors the JSON bundle produced by the
CPython helper `www2json.py` (`{"type", "attributes", "text", "children"}`),
lowered to a shape the rpython -> C translator keeps fast and predictable:

* **One class, one hierarchy.** The element *kind* is a `char*` field
  (`tag_name`), not a subclass -- the whole tree is a single type and the
  renderer dispatches on `tag()` at runtime. Keeping DOM nodes in their own
  single-rooted hierarchy, never mixed into rpyqt's `_QtObject` widget
  hierarchy, is what lets both co-compile with consistent vtables.

* **Promoted attributes, no dict.** The HTML attributes the renderer actually
  consumes (`href`, `name`, `value`, `type`, `onclick`, `placeholder`, `src`)
  are plain `char*` fields, so every access is a direct struct read -- no
  heterogeneous dict, no micropython object-core fallback.

* **`list[obj]` children.** Children live in the same boxed-list container
  rpyqt uses for layout items, so the tree nests uniformly and a child pulled
  back out as `obj` still resolves its `Node` methods.

Nodes are built with concrete locals and direct field writes -- e.g.

    n = Node("a"); n.text = "home"; n.href = "/"; parent.add(n)

which lowers to a `malloc` + struct stores + a boxed `list.append`. That is
exactly the code `www2json.py` generates into `page_data.py`, and what the
sample page in `json2qt.py` writes by hand. (Free helpers that *returned* a
bare cross-module `Node` are avoided: the translator does not module-qualify a
bare class name in a free function's signature, so we keep construction inline
where the local's type is inferred from the `Node(...)` call.)
"""


class Node:
    def __init__(self, tag: "char*"):
        self.tag_name = tag
        self.text = ""
        self.href = ""
        self.name = ""
        self.value = ""
        self.itype = ""          # <input type=...>
        self.onclick = ""
        self.placeholder = ""
        self.src = ""
        self.eid = ""            # the HTML id attribute (for getElementById)
        self.shandle = ""        # the minidom element handle (for two-way sync)
        self.children: "list[obj]" = []

    # --- kind / text -----------------------------------------------------
    def tag(self) -> "char*":
        return self.tag_name

    def get_text(self) -> "char*":
        return self.text

    # --- attributes ------------------------------------------------------
    def get_href(self) -> "char*":
        return self.href

    def get_name(self) -> "char*":
        return self.name

    def get_value(self) -> "char*":
        return self.value

    def get_itype(self) -> "char*":
        return self.itype

    def get_onclick(self) -> "char*":
        return self.onclick

    def get_id(self) -> "char*":
        return self.eid

    def get_shandle(self) -> "char*":
        return self.shandle

    def get_placeholder(self) -> "char*":
        return self.placeholder

    def get_src(self) -> "char*":
        return self.src

    # --- children --------------------------------------------------------
    def child_count(self) -> int:
        return len(self.children)

    def child(self, i: int) -> "obj":
        return self.children[i]

    def add(self, ch: "obj") -> None:
        self.children.append(ch)
