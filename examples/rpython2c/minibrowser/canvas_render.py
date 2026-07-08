#!/usr/bin/env python3
"""Render the page's native <script type="rpython"> shader off-target (CPython).

Same pipeline as the browser (extract -> jitc -> ctypes), calling the JIT'd
`pixel(x, y, t, mx, my)` shader with real ctypes. Writes an animated GIF (frames
at advancing time t) so the native "drawing on the page" -- and its animation --
is viewable without a compositor.

    python3 canvas_render.py [out.gif] [frames]
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
    dll.render.restype = ctypes.c_int
    dll.render.argtypes = [ctypes.POINTER(ctypes.c_uint), ctypes.c_int,
                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]

    out = argv[1] if len(argv) > 1 else os.path.join(HERE, "canvas.gif")
    nframes = int(argv[2]) if len(argv) > 2 else 24
    import math
    buf = (ctypes.c_uint * (W * H))()
    frames = []
    for t in range(nframes):
        # move the "pointer" in a circle so both uniforms are visible
        ang = 2 * math.pi * t / nframes
        mx = int(W / 2 + W / 3 * math.cos(ang))
        my = int(H / 2 + H / 3 * math.sin(ang))
        dll.render(buf, W, H, t * 4, mx, my)      # one native call fills a frame
        img = Image.new("RGB", (W, H))
        px = img.load()
        for y in range(H):
            for x in range(W):
                v = buf[y * W + x] & 0xFFFFFFFF
                px[x, y] = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
        frames.append(img.resize((W * 3, H * 3), Image.NEAREST))
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=60, loop=0)
    print("wrote", out, "(%d frames)" % nframes)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
