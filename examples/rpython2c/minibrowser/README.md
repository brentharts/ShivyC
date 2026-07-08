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

## Python scripting via embedded minipy

A page's `<script type="python">` runs on **minipy** — the RPython Python
interpreter (`tools/minipy`, see `MINIPY.md`) — co-compiled *into the browser
binary*. The split mirrors the renderer's own CPython/native halves:

* **Offline (CPython).** `www2json.py` captures `<script type="python">` bodies
  (into the bundle's `python` field, separate from other `scripts`) plus element
  `id`s and inline `onclick` handlers. At page load the browser shells out to
  **`pycompile.py`**, which assembles one minipy program — the **`minidom.py`**
  prelude (`document` / `console` / `window`, written in the minipy subset) +
  one `document._register(Element(...))` per id'd element + the page script —
  and compiles it to `page.mpyc` bytecode.
* **Runtime (native).** The embedded interpreter `mpy_boot`s that bytecode once
  (defining the page's functions and the DOM globals), and a button's `onclick`
  fires the named handler through `mpy_call` — so a click runs Python against
  the DOM. Booting is **lazy**: scriptless pages never invoke the interpreter.

`interp.py` grows only a small, additive embedding facade (`build_state`,
`mpy_boot`, `mpy_call`) that keeps its `Program`/`St` types on the interpreter
side of the translation-unit boundary (callers pass only `char*`/`int`); the
3-way minipy suite is unaffected. The interpreter's `main()` is stripped for the
co-compiled copy (`gen_embed.py` → `interp_embed.py`, a build artifact) because
py2c turns any `main` into the C entry point and `json2qt` already provides one.

Try it headless (no compositor, no click needed):

```
make minibrowser
cd build/gui && ./minibrowser_app --script-selftest
```

which loads `pyscript2.html`, boots it, then drives events by element handle
(as clicks would) and reads the DOM back after each one:

```
initial dom: {"type":"body",...,"children":[{"type":"button",...,"text":"clickme"}]}
hello minipy console
input value after foo: hello
input value after bar: world
```

### Live DOM mutation

The DOM is authoritative *inside* minipy: page scripts mutate ordinary
interpreted-Python objects (`minidom.py` — `document`, `Element`, `console`,
`window`), and the browser renders from what they report. After boot and after
every event the browser calls `__serialize()` to get `document.body` as JSON
(the same shape `minijson` already parses), rebuilds the widget tree, and
surfaces `console.log` / `window.alert` output on screen. This side-steps the
hard direction (native → interpreter) entirely: the browser only ever reads a
string back.

So the full example works live —

```python
def foo():
    iput = document.createElement('input')   # create
    iput.value = 'hello'                       # set value
    iput.setAttribute('id', 'INPUT')
    document.body.appendChild(iput)            # append -> a real widget appears
    btn = document.createElement('button')
    btn.onclick = bar                          # a Python callable as the handler
    document.body.appendChild(btn)
def bar():
    document.getElementById('INPUT').value = 'world'   # mutate -> widget updates
```

Events reach back through **integer handles**: every element gets a unique one,
an element with an onclick serializes `"onclick":"<handle>"`, and clicking it
calls `mpy_call_i("__fire", handle)` — which runs that element's onclick (a
string handler like `foo()` wired by `pycompile` to `el.onclick = foo`, or a
script-assigned callable like `btn.onclick = bar`) and triggers a re-render.
`pyscript_test.py` is the same proof at the source level (assemble → native
minipy → assert).

Two minipy-subset constraints shaped `minidom.py`: a function stored in an
attribute can't be called as `obj.cb()` (that parses as a method lookup) so
`_fire` binds it to a local first (`cb = e.onclick; cb()`), and mutable
counters/registries live as object fields, not module globals.

## Native code in the page (a JIT alternative to wasm)

A page can ship **native code** in `<script type="rpython" id="NAME">` and call it
from its python. Instead of a stack-based wasm VM with JS wrappers for every DOM
touch, each rpython block is compiled *to a native shared object* and loaded via
`ctypes` — faster (real registers/SIMD, `gcc -O2`) and backwards-compatible with
CPython:

```html
<script type="rpython" id="foo">
def calc_sum(a: int, b: int) -> int:
    return a + b
</script>
<script type="python">
import ctypes
dll = ctypes.CDLL('/tmp/jit.foo.so')
def foo():
    v = dll.calc_sum(1, 2)     # native call, no VM
    console.log(v)             # -> 3
</script>
```

The pipeline (`www2json` → `jitc.py`):

* `www2json` captures each `<script type="rpython" id=..>` into the bundle's
  `rpython` map, keyed by id.
* `jitc.py` compiles each block with **py2c → `gcc -O2 -shared -fPIC`** into
  `/tmp/minibrowser_cache/<page-id>/jit.<name>.so`, cached by a sha256 of the
  block source. rpython translation is slow, so an unchanged block is a cache
  hit on reload (only a first visit pays the compile). Per-page dirs keep caches
  from colliding across sites, so the source's portable `'/tmp/jit.foo.so'` is
  redirected to that page's cache at load.

Because the model is just "native `.so` + ctypes", it is also the path to APIs
the renderer doesn't implement yet (Canvas2D, WebGL, WebGPU): expose them as
symbols an rpython block calls. And unlike a JIT'd JS engine, memory only spikes
while py2c + gcc run, not for the page's lifetime.

Two proofs ship, run headless:

```
python3 jit_test.py     # CPython + real ctypes: extract -> JIT -> cache -> calc_sum(1,2)=3
python3 ffi_test.py     # native: transpiled RPython dlopens the .so and calls it at run time
```

`jit_test.py` runs the page's *exact* source (`ctypes.CDLL(...)`, `dll.calc_sum`)
on CPython. `ffi_test.py` proves the native run-time path the embedded
interpreter uses: `mb_ffi.c` (in `tools/rpy_lib/`, linked into the browser)
exposes `mb_dlopen` / `mb_dlsym` / `mb_callNi`, which transpiled RPython reaches
through the ctypes *static* bridge while the JIT'd `.so` is `dlopen`ed
dynamically (`dlopen` is in libc on this glibc, so no `-ldl`).

**This runs natively in the browser too.** The interpreter carries ctypes FFI
builtins (`interp.py`, guarded so it still imports under CPython); and because
minipy has no `__getattr__` to dispatch `dll.calc_sum` on the handle at run
time, `pycompile` lowers it at compile time onto those builtins:

```
import ctypes                          ->  (dropped)
dll = ctypes.CDLL('/tmp/jit.foo.so')   ->  dll = _ffi_open('<page-cache>/jit.foo.so')
dll.calc_sum(1, 2)                     ->  _ffi_call2(dll, 'calc_sum', 1, 2)
```

So on a scripted page's load `pycompile` JIT-compiles the rpython blocks (via
`jitc`), rewrites the ctypes onto the shim, and boots it; a click then runs the
native `calc_sum` from inside minipy. Proven headless:

```
make minibrowser
cd build/gui && ./minibrowser_app --jit-selftest    # -> console: hello minipy console / 3
```

The unrewritten page source still runs under CPython with real ctypes
(`jit_test.py`), so the same page is portable to a stock Python + ctypes host.

### Native drawing (a canvas fragment shader)

Native code can also *draw* to the page, and animate. A `<canvas>` is filled by a
native `render(buf, w, h, t, mx, my)` shader shipped as a `<script
type="rpython">` block -- with a **time** uniform `t` and a **pointer** uniform
`(mx, my)`. It fills a whole ARGB buffer in **one native call per frame** (not
per-pixel FFI), writing directly into `buf`:

```html
<script type="rpython" id="shader">
def render(buf: "u32*", w: int, h: int, t: int, mx: int, my: int) -> int:
    y = 0
    while y < h:
        x = 0
        while x < w:
            r = (x * 5 + t * 3) & 255       # shifts with time
            g = (y * 5 + t * 2) & 255
            b = ((x + y) * 3 + t) & 255
            dx = x - mx
            dy = y - my
            if dx * dx + dy * dy < 200:      # brighten near the pointer
                r = 255
                g = 255
            buf[y * w + x] = (255 << 24) | (r << 16) | (g << 8) | b
            x = x + 1
        y = y + 1
    return 0
</script>
<canvas id="cvs" width="96" height="96"></canvas>
```

The browser JIT-compiles the block (`jitc`), allocates one native ARGB buffer
(`mb_canvas_alloc`, reused across frames), and calls `render` through the FFI
shim (`mb_render_call`). The buffer never enters the interpreter: `QCanvas` reads
it back through a `u32*` view and blits it -- so a frame is **two native calls**
(fill + blit), not `w*h` per-pixel FFI calls.

**Animation** is driven by a Wayland **frame-callback loop** added to the
runtime: while an animated widget is on screen (`rw_wants_frame()` returns 1),
each frame calls `rw_frame(px, py)`, which advances `t`, refills the canvas with
the current pointer, and repaints. Pages with nothing animating stay purely
event-driven -- the loop is opt-in, so the other GUI examples are unaffected.

```
cd build/gui && ./minibrowser_app --canvas-selftest   # shader animates + reacts to pointer
python3 canvas_render.py canvas.gif                    # same shader -> an animated GIF
```

`canvas_render.py` runs the identical shader under CPython + real ctypes over a
range of `t` (and a moving pointer) and saves an animated GIF, so the native
output is viewable off-target.

## JavaScript on the same engine (js2py)

Plain `<script>` JavaScript runs on the *same* minipy engine and DOM as
`<script type="python">`. `js2py.py` parses the JS with **pyjsparser** (the
parser the [Js2Py](https://github.com/OpenSourceJesus/Js2Py) project is built
on) and translates the ESTree AST into minipy-subset Python against the minidom,
so `www2json` folds it into the page's `python` field:

```html
<script>
function greet() {
    console.log('hello from javascript');
    document.getElementById('OUT').value = 'set by JS';
}
</script>
<button onclick="greet()">click me</button>
<input id="OUT" value="unset">
```

becomes, transparently:

```python
def greet():
    console.log("hello from javascript")
    document.getElementById("OUT").value = "set by JS"
```

and the click runs it -- console line printed, input value mutated -- exactly as
the Python path would. The translator covers a pragmatic subset (functions,
`var`/`let`/`const`, `if`/`while`/C-style `for`, `return`, member/call/assign
expressions, `===`->`==`, `&&`->`and`, `?:`->`if/else`), plus **object literals**
(→ dicts, so `o.a` reads `o["a"]`) and the common **array methods**
(`push`->`append`, `pop`, `shift`, `unshift`, `.length`->`len`). Unsupported
constructs are skipped (the JS stays in `scripts`, unrun) rather than breaking
the page. Full ECMAScript semantics -- `+` type coercion, hoisting, closures,
prototypes, `this` -- are out of scope; the goal is DOM-scripting parity, not a
JS VM.

```
python3 js_test.py                                # translate + run on native minipy
cd build/gui && ./minibrowser_app --js-selftest   # JS greet() runs in the browser
```

Needs `pip install pyjsparser` (optional, like Js2Py itself); without it the JS
is simply left unrun.

## TypeScript compiled to native (ts2py)

Everybody compiles TypeScript to JavaScript. We do better: a `<script
type="typescript">` block is translated to **typed rpython** and JIT-compiled to
a native `.so` (the same path as `<script type="rpython">`), so a typed TS
function becomes native machine code, not interpreted JavaScript. TypeScript's
type annotations are exactly what py2c needs -- `number` -> `int`, `boolean` ->
`bool`, `string` -> `str`, `void` -> `None`, `T[]` -> `list[t]`:

```html
<script type="typescript" id="tsmod">
function fib(n: number): number {
    let a: number = 0;
    let b: number = 1;
    for (let i: number = 0; i < n; i++) {
        let t: number = a + b;
        a = b;
        b = t;
    }
    return a;
}
</script>
<script type="python">
import ctypes
dll = ctypes.CDLL('/tmp/jit.tsmod.so')
def run():
    console.log(dll.fib(10))      # 55, computed by native code
</script>
```

`www2json` routes the block into the page's rpython map; `ts2py` translates it
(`def fib(n: int) -> int: ...`), and `jitc` compiles it to `jit.tsmod.so`, which
the page's Python calls via ctypes like any native block.

`ts2py.py` is a dependency-free, pure-Python TypeScript front end (no Node, no
npm, no `typescript` compiler): its own tokenizer and a precedence-climbing
expression parser cover typed function declarations, typed locals, the usual
control flow, and the operator set (`===`->`==`, `&&`->`and`, `i++`->`i = i + 1`).
It is a subset aimed at native page functions; a block it can't translate is
skipped rather than mistranslated. Numeric work maps to `int` today (TS's single
`number` type); floats and string returns are future.

```
python3 ts_test.py                                # translate + www2json routing
cd build/gui && ./minibrowser_app --ts-selftest   # TS fib(10)=55, native, in-browser
```

## Two-way input binding

Typing into an `<input>` flows back into the DOM. Each rendered field is bound to
its minidom element by handle; on a keystroke `QLineEdit` records the edit and
`json2qt` pushes the new text into the DOM (`mpy_call_is("__set_value", handle,
text)`) without a re-render (so focus survives). A script then reads the typed
value:

```html
<input id="INPUT" value="">
<button onclick="show()">show</button>
<script type="python">
def show():
    console.log('input is: ' + document.getElementById('INPUT').value)
</script>
```

Type `hi`, click *show*, and the console prints `input is: hi` -- the value the
user typed, read from the DOM. Verified headless (driving the real
`on_key -> textChanged -> on_edit -> minidom` path):

```
cd build/gui && ./minibrowser_app --twoway-selftest
```

## Roadmap (deliberately not yet done)

* **More DOM.** `removeChild`, more attributes, and a real modal `alert` overlay
  (rather than an on-screen label) are the next DOM gaps.
* **Richer canvas.** Configurable size / `data-shader` read from the element,
  and click/drag state beyond the pointer position.
* **Wider JS coverage.** The js2py subset now handles DOM scripting, objects, and
  arrays; JS `+` string coercion, `this`/closures, and array iteration methods
  (`map`/`forEach`) are the next reach.
* **Wider TypeScript coverage.** `ts2py` compiles typed numeric functions to
  native code; `float`/`number` distinction, string returns across the FFI,
  interfaces/type aliases, and arrow functions are the next reach.
* **Real network fetch.** `www2json --url` exists; wiring it into `navigate()`
  (rather than local `*.html`) is a small step where the network is available.
* **Images beyond a placeholder**, and a fuller keymap (other layouts, via
  xkbcommon) than the built-in US map.
