# minibrowser — a mini web-browser in rpython

A tiny web browser whose **renderer is pure rpython**, transpiled to a native
Wayland client by `tools/py2c.py` (no Qt linked, all C generated). It is a port
of OpenSourceJesus's Tetra [`json2qt.py`](https://github.com/OpenSourceJesus/Tetra/blob/main/json2qt.py),
which does the same job under CPython/PyQt5.

The design splits cleanly into a *dynamic* CPython half and a *static* rpython
half:

```
  HTML + JS ──▶ www2json.py ──▶ page.json      (canonical bundle)
                (CPython)   └─▶ page_data.py   (the "py" form of the page)
                                     │
        dom.py  +  json2qt.py  +  page_data.py     ── one py2c translation unit ──▶  native
       (Node tree)  (renderer)   (generated)                                        Wayland app
                        │
                     rpyqt  (widgets, software-rendered on rwayland)
```

* **`www2json.py`** runs under ordinary **CPython** (full stdlib allowed). It
  parses HTML, captures page scripts, and writes two things: `page.json` (the
  bundle, same `{source,title,dom,scripts}` shape as Tetra) and `page_data.py`
  — the *rpython* form of the page, a `build_page()` that constructs the DOM
  with concrete `Node(...)` locals. This is the "json/py" the renderer eats.
* **`dom.py`** is the rpython `Node` model: one class, one hierarchy, element
  kind as a `char*` field, the handful of consumed attributes promoted to typed
  `char*` fields (no dict), children in a `list[obj]`.
* **`json2qt.py`** is the port of Tetra's `generate_interface`: it walks the
  `Node` tree and builds an `rpyqt` widget tree, dispatching on `Node.tag()`.
* **`rpyqt`** (in `tools/rpy_lib/rpyqt.py`) is the widget layer, software-
  rendered into the `rwayland` framebuffer.

## Build & run

```sh
make minibrowser          # regenerates page_data.py, transpiles, links wl
./build/gui/minibrowser_app   # run under a Wayland compositor
```

`make minibrowser` first runs `www2json.py example.html` to (re)generate
`page.json` + `page_data.py`, then co-compiles `json2qt.py dom.py page_data.py`
(rpyqt is bundled automatically and the Wayland glue is generated). Point it at
another page by editing the `www2json.py` line, or run it by hand:

```sh
python3 www2json.py some_page.html --out .
python3 www2json.py --url https://example.com --out .
```

## Off-target check (no compositor needed)

Because the rpython dialect is a Python subset, the renderer also runs under
CPython. `render_test.py` builds the page, paints into a framebuffer, asserts it
draws and that a button click reaches its handler, and can dump a screenshot:

```sh
python3 render_test.py            # asserts
python3 render_test.py out.png    # + PNG (needs Pillow)
```

## What this port covers

`json2qt.py` renders these DOM node kinds: structural containers
(`html/head/body/div/span/section/...`), headings `h1`–`h6`, paragraphs
(`p/blockquote/cite/dt/dd/figcaption`), anchors `a`, lists `ul/ol/li`, rules
`hr`, breaks `br`, images (as a placeholder), `form`/`figure` sub-layouts, text
inputs and submit/buttons (`input`, `button`), and `table/tr/td/th` as a grid of
rows. Link/button/field activations are wired through the Wayland event loop to
a shared handler (a visible status line), proving the signal path end to end.

### rpyqt widgets added for this port

To render the above, `tools/rpy_lib/rpyqt.py` gained: **lowercase + basic
punctuation** in the 5×7 font (web text is mostly lowercase); **`QLineEdit`** (a
bordered text field with placeholder + a `returnPressed` signal); **`QHeading`**
(scaled headings); **`QLink`** (link-coloured, underlined, clickable anchor);
and **`QHLine`** (an `<hr>` rule). All stay within rpyqt's single `_QtObject`
hierarchy so vtables remain consistent under py2c.

## Roadmap (deliberately not yet done)

* **Runtime JSON in rpython.** Today the page reaches the renderer as generated
  `page_data.py` (co-compiled). A small rpython JSON reader would let the binary
  load `page.json` at runtime instead of at build time.
* **Keyboard in rwayland.** The generated Wayland runtime currently delivers
  only pointer events, so `QLineEdit` shows text and treats a click as submit
  but cannot yet *type*. Real editing needs a `wl_keyboard` path in the runtime
  plus a `rw_key` hook and an `on_key` on `rwayland.Window` — this is the main
  **rwayland** extension the browser is waiting on.
* **Live navigation.** Wire `QLink.clicked` / form submit to re-run `www2json`
  on the target and rebuild the tree (back/forward stacks).
* **Scripting via minipy.** Page scripts are captured but not run. The plan is
  to translate JS → Python with OpenSourceJesus's [Js2Py fork](https://github.com/OpenSourceJesus/Js2Py)
  and execute it through **embedded minipy** (see `MINIPY.md`) as the page's
  scripting engine — Python instead of JavaScript, on the same DOM.
