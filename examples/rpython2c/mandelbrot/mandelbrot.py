"""Mandelbrot set rendered to a binary PPM image (rpython -> C, no runtime).

Exercises the float-local inference (the escape iteration is pure double math),
typed C arrays (the RGB byte buffer), nested range loops, and binary file I/O
(`f.write(buf, n)` -> fwrite). The exit code is a checksum of the pixels so the
render is verifiable without reading the file back.

    python3 -m shivyc.main --no-cache mandelbrot.py -o /tmp/mandel && /tmp/mandel
    #  writes /tmp/mandel.ppm  (256x256 P6)
"""


def main() -> int:
    buf: "char*" = malloc(256 * 256 * 3)
    total = 0
    for py in range(256):
        for px in range(256):
            x0 = (px / 128.0) - 2.0          # real axis  [-2.0, 0.0]
            y0 = (py / 128.0) - 1.0          # imag axis  [-1.0, 1.0]
            x = 0.0
            y = 0.0
            iters = 0
            while x * x + y * y <= 4.0 and iters < 255:
                xt = x * x - y * y + x0
                y = 2.0 * x * y + y0
                x = xt
                iters = iters + 1
            idx = (py * 256 + px) * 3
            buf[idx] = iters                 # R: escape time
            buf[idx + 1] = (iters * 5)       # G
            buf[idx + 2] = (255 - iters)     # B
            total = total + iters
    f = open("/tmp/mandel.ppm", "w")
    f.write("P6\n256 256\n255\n")
    f.write(buf, 256 * 256 * 3)
    f.close()
    return total % 256
