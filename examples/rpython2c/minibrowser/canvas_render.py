#!/usr/bin/env python3
"""Render the page's native <script type="rpython"> shader to a PNG under CPython.

Same pipeline as the browser (extract -> jitc -> ctypes), but calls the JIT'd
`pixel(x, y)` per pixel with real ctypes and saves the image -- so the native
"drawing on the page" is viewable off-target. Proves the canvas output visually
and that the shader is CPython/ctypes-portable.

    python3 canvas_render.py [out.png]
"""
import ctypes
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
W = H = 96


def main(argv):
    sys.path.insert(0, HERE)
    import www2json
    import jitc
    from PIL import Image

    bundle = www2json.build_bundle("canvas.html",
                                   open(os.path.join(HERE, "canvas.html")).read())
    cache_dir, results = jitc.compile_page(bundle, "canvas")
    so = os.path.join(cache_dir, "jit.shader.so")
    print("JIT'd shader:", so, results.get("shader", ("", "?"))[1])

    dll = ctypes.CDLL(so)
    dll.pixel.restype = ctypes.c_int
    dll.pixel.argtypes = [ctypes.c_int, ctypes.c_int]

    img = Image.new("RGB", (W, H))
    for y in range(H):
        for x in range(W):
            v = dll.pixel(x, y) & 0xFFFFFFFF
            img.putpixel((x, y), ((v >> 16) & 255, (v >> 8) & 255, v & 255))
    out = argv[1] if len(argv) > 1 else os.path.join(HERE, "canvas.png")
    img.save(out)
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
