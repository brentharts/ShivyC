# minibrowser — a mini web-browser in rpython

A tiny web browser whose **renderer is pure rpython**, transpiled to a native
Wayland client by `tools/py2c.py` (no Qt linked, all C generated). It is a port
of OpenSourceJesus's Tetra [`json2qt.py`](https://github.com/OpenSourceJesus/Tetra/blob/main/json2qt.py),
which does the same job under CPython/PyQt5.

The design splits cleanly into a *dynamic* CPython half and a *static* rpython
half, joined at runtime by a `page.json` bundle:

```
  HTML + JS ──▶ www2json.py ──▶ page.json      (canonical bundle, on disk)
                (CPython)                              │
                                                       │  read at runtime
        dom.py + json2qt.py + minijson.py   ── one py2c unit ──▶  native Wayland app
       (Node tree) (renderer) (json reader)                            │
                        │                                        loads page.json,
                     rpyqt  (widgets, software-rendered)         renders, navigates
```

* **`www2json.py`** runs under ordinary **CPython** (full stdlib allowed). It
  parses HTML, captures page scripts, and writes `page.json` (the bundle, same
  `{source,title,dom,scripts}` shape as Tetra). It can also emit `page_data.py`
  (a co-compilable `build_page()`), but the renderer no longer needs that: it
  reads the JSON at runtime.
* **`dom.py`** is the rpython `Node` model: one class, one hierarchy, element
  kind as a `char*` field, the handful of consumed attributes promoted to typed
  `char*` fields (no dict), children in a `list[obj]`.
* **`minijson.py`** is a tiny **runtime** JSON reader: it parses a `page.json`
  bundle from disk into the `Node` tree, so the same binary can display any page
  `www2json.py` produces (and is what live navigation stands on).
* **`json2qt.py`** is the port of Tetra's `generate_interface`: it walks the
  `Node` tree and builds an `rpyqt` widget tree, dispatching on `Node.tag()`,
  and adds the navigation toolbar + link/URL-bar handling.
* **`rpyqt`** (in `tools/rpy_lib/rpyqt.py`) is the widget layer, software-
  rendered into the `rwayland` framebuffer.

## Build & run

```sh
make minibrowser                 # transpiles json2qt+dom+minijson, links wl,
                                 # and stages the site next to the binary
cd build/gui && ./minibrowser_app   # run under a Wayland compositor
```

The binary loads a page by shelling out to `python3 www2json.py <name>.html` at
runtime and parsing the resulting `page.json`, so it must run from a directory
containing `www2json.py` and the `*.html` site — `make minibrowser` stages
`www2json.py`, `home.html`, and `about.html` next to the binary for you. Point
it at other local pages by dropping their `.html` in that directory (a live
`--url` fetch uses the same `www2json` path where the network is available).

## Off-target check (no compositor needed)

Because the rpython dialect is a Python subset, the renderer also runs under
CPython. `render_test.py` drives the whole navigation loop off-target — loads
`home`, asserts it draws, clicks the in-page About link and asserts the About
page loads, types a name + Enter in the URL bar, and checks Back — and can dump
a screenshot:

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
bordered text field with placeholder, keyboard focus, a caret, and a
`returnPressed` signal); **`QHeading`** (scaled headings); **`QLink`** (link-
coloured, underlined, clickable anchor); and **`QHLine`** (an `<hr>` rule). All
stay within rpyqt's single `_QtObject` hierarchy so vtables remain consistent
under py2c.

### Keyboard input

Text fields really type. The generated Wayland runtime now binds `wl_seat`'s
keyboard, maps evdev keycodes to ASCII for a US layout (dependency-free — shift
is tracked from the physical shift keys, no xkbcommon), and calls a sixth
`rw_key(codepoint, pressed)` hook. Clicking a `QLineEdit` focuses it (blue
border + caret); printable keys append, backspace deletes, and Enter emits
`returnPressed`. `render_test.py` drives this whole path (pointer-focus → keys →
text) off-target as a regression check.

### Runtime JSON + live navigation

The page is no longer baked in at build time. `minijson.py` parses a `page.json`
bundle from disk at runtime into the `Node` tree — a small schema-specific
recursive-descent parser (objects/arrays/strings, `\uXXXX` folded to `?`), where
each parse method returns one concrete rpython type so it lowers to direct C.

On top of that, `json2qt.py` adds a real browser loop. A toolbar (Back / URL
field / GO) sits above the page. Clicking an in-page link, or typing a page name
+ Enter in the URL bar, calls `navigate()`, which re-runs `www2json.py` on the
target (via `os.system`) to (re)produce `page.json`, parses it, and rebuilds the
window's layout in place; a back-stack drives the Back button. Getting this to
lower cleanly drove a few rpython-dialect findings: navigation state lives on an
object-model class behind a module global (a module-global *list* miscompiles);
`QWidget.setLayout` takes a concrete `QBoxLayout` (not `obj`) so it can be called
on a window held in a global without the boxed-arg mis-lowering; and strings
read off an `obj`-typed `Node` are bound to explicitly annotated `char*` locals
before use. A method name shared across the `dom.Node` and `rpyqt` hierarchies
(`get_href`) made obj-dispatch pick the wrong vtable, so rpyqt's is `href_value`.

## Roadmap (deliberately not yet done)

* **Scripting via minipy.** Page scripts are captured but not run. The plan is
  to translate JS → Python with OpenSourceJesus's [Js2Py fork](https://github.com/OpenSourceJesus/Js2Py)
  and execute it through **embedded minipy** (see `MINIPY.md`) as the page's
  scripting engine — Python instead of JavaScript, on the same DOM.
* **Real network fetch.** `www2json --url` exists; wiring it into `navigate()`
  (rather than local `*.html`) is a small step where the network is available.
* **Images beyond a placeholder**, and a fuller keymap (other layouts, via
  xkbcommon) than the built-in US map.
