"""rwayland -- first-class Wayland for the rpython dialect (no C glue).

Write a Wayland GUI in pure rpython. Subclass `Window`, draw into a framebuffer
in `on_paint`, react to clicks in `on_pointer_button`, and call `.run()`:

    import rwayland

    class App(rwayland.Window):
        def __init__(self):
            super().__init__(400, 300, "rpython Wayland")
            self.pressed = 0

        def on_paint(self, fb: "u32*") -> None:
            rwayland.fill(fb, self.width, self.height, 0xFFE0E0E0)

        def on_pointer_button(self, x: int, y: int, pressed: int) -> int:
            self.pressed = pressed
            return rwayland.ACTION_REDRAW

        def on_key(self, codepoint: int, pressed: int) -> int:
            return rwayland.ACTION_NONE

    def main() -> int:
        return App().run()

How the glue disappears
-----------------------
The Wayland protocol boilerplate -- listener vtables, shm buffers, the registry,
the dispatch loop -- is *generated* by py2c, not written by hand. When a program
imports this module, py2c writes an app-agnostic C runtime (`rwayland_rt.{h,c}`,
plus the scanned `xdg-shell` files) to the output directory and links
`-lwayland-client`. That runtime drives Wayland and calls back into the six
`rw_*` trampolines below, which forward to the currently-running `Window`.

So the user side is entirely rpython; the only C is machine-generated and the
same for every program.

Dual mode
---------
Under py2c the `rwl_run` call is lowered to a direct C call into the generated
runtime via the ctypes FFI bridge. Under CPython the same import still parses and
the pure-Python helpers (`fill`, `fill_rect`) run, so drawing logic can be unit
-tested off-target; `run()` raises there because there is no generated runtime.
"""

import rpy_ctypes as ctypes

# The generated runtime exposes rwl_run(); the link target also pulls in
# libwayland-client. (The symbol itself is resolved from rwayland_rt.o.)
_wl = ctypes.CDLL("libwayland-client.so.0")
_wl.rwl_run.restype = ctypes.c_int
_wl.rwl_run.argtypes = []

# Pointer-button action codes returned by on_pointer_button (must match the
# RWL_* values in rwayland_rt.h).
ACTION_QUIT = 0
ACTION_MOVE = 1
ACTION_REDRAW = 2
ACTION_NONE = 3


class Window:
    """A single top-level Wayland window backed by a software framebuffer."""

    def __init__(self, width: int, height: int, title: "char*"):
        self.width = width
        self.height = height
        self.title = title

    def on_paint(self, fb: "u32*") -> None:
        """Override: paint the whole framebuffer (XRGB8888, width*height)."""
        fill(fb, self.width, self.height, 0xFF000000)

    def on_pointer_button(self, x: int, y: int, pressed: int) -> int:
        """Override: handle a left-button press/release; return an ACTION_*."""
        return ACTION_NONE

    def on_key(self, codepoint: int, pressed: int) -> int:
        """Override: handle a key event. `codepoint` is ASCII (8=backspace,
        13=enter, >=32 printable); `pressed` is 1 on press, 0 on release."""
        return ACTION_NONE

    def run(self) -> int:
        """Hand control to the generated Wayland runtime (blocks until close)."""
        set_active(self)
        return _wl.rwl_run()


# --- drawing helpers (pure rpython, fused-loop friendly) -----------------
def fill(fb: "u32*", w: int, h: int, color: int) -> None:
    """Fill the entire framebuffer with one color."""
    n = w * h
    i = 0
    while i < n:
        fb[i] = color
        i = i + 1


def fill_rect(fb: "u32*", fb_w: int, fb_h: int,
              x: int, y: int, w: int, h: int, color: int) -> None:
    """Fill an axis-aligned rectangle, clipped to the framebuffer."""
    i = y
    while i < y + h:
        j = x
        while j < x + w:
            if j >= 0 and j < fb_w and i >= 0 and i < fb_h:
                fb[i * fb_w + j] = color
            j = j + 1
        i = i + 1


# --- the rw_* trampolines the generated runtime calls --------------------
# A single active window is held in a module global. The generated C runtime
# calls these six externs; each forwards to the active Window. Their C symbols
# are exactly rw_width/rw_height/rw_title/rw_paint/rw_pointer/rw_key (unique
# names -> unmangled), matching the contract in rwayland_rt.h.
_active: "Window*" = None


def set_active(w: "Window*") -> None:
    global _active
    _active = w


def rw_width() -> int:
    return _active.width


def rw_height() -> int:
    return _active.height


def rw_title() -> "char*":
    return _active.title


def rw_paint(fb: "u32*") -> None:
    _active.on_paint(fb)


def rw_pointer(x: int, y: int, pressed: int) -> int:
    return _active.on_pointer_button(x, y, pressed)


def rw_key(codepoint: int, pressed: int) -> int:
    return _active.on_key(codepoint, pressed)


def rw_frame(px: int, py: int) -> int:
    # Base Window is event-driven; the frame-callback loop stays off.
    return ACTION_NONE


def rw_wants_frame() -> int:
    return 0
