"""SVG line-chart builder -- string formatting + concatenation under load.

Builds an SVG polyline plot of N points of a damped sine and returns the byte
length of the rendered document mod 256. Every point becomes a `"%.2f,%.2f"`
coordinate, every chart element a `%`-formatted tag, and the whole thing is
joined into one string -- so this is the suite's string / `printf`-formatting
benchmark, a path the purely numeric benchmarks never exercise. Point count N is
read from argv.

It mirrors the work of the bundled `rpy_plot` renderer (same coordinate mapping
and `%`-formatting), but is self-contained so the cross-runtime harness runs it
directly on every backend. The floating-point coordinate math makes the rendered
bytes deterministic, so all four backends must agree on the length.
"""
import sys


def map_x(x: float, x0: float, x1: float, px0: float, px1: float) -> float:
    if x1 == x0:
        return (px0 + px1) * 0.5
    return px0 + (x - x0) * (px1 - px0) / (x1 - x0)


def map_y(y: float, y0: float, y1: float, py0: float, py1: float) -> float:
    if y1 == y0:
        return (py0 + py1) * 0.5
    return py1 - (y - y0) * (py1 - py0) / (y1 - y0)


def build(n: int) -> "char*":
    # damped sine sampled on [0, 12); deterministic doubles
    xs: "list[float]" = []
    ys: "list[float]" = []
    i = 0
    while i < n:
        x = 12.0 * i / n
        # a cheap sine via a few terms is overkill; use a polynomial wiggle that
        # stays deterministic across backends without libm
        t = x - 6.0
        y = (t * t * t - 30.0 * t) / (1.0 + 0.2 * t * t)
        xs.append(x)
        ys.append(y)
        i = i + 1

    # axis bounds with a manual min/max scan (no min()/max() over typed lists)
    xlo = xs[0]
    xhi = xs[0]
    ylo = ys[0]
    yhi = ys[0]
    i = 1
    while i < n:
        if xs[i] < xlo:
            xlo = xs[i]
        if xs[i] > xhi:
            xhi = xs[i]
        if ys[i] < ylo:
            ylo = ys[i]
        if ys[i] > yhi:
            yhi = ys[i]
        i = i + 1

    pl = 48.0
    pr = 592.0
    pt = 48.0
    pb = 432.0

    parts: "list[char*]" = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480">')
    parts.append('<rect width="100%" height="100%" fill="white"/>')
    parts.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" fill="none" stroke="#222222" stroke-width="1"/>'
                 % (pl, pt, pr - pl, pb - pt))

    coords: "list[char*]" = []
    i = 0
    while i < n:
        px = map_x(xs[i], xlo, xhi, pl, pr)
        py = map_y(ys[i], ylo, yhi, pt, pb)
        coords.append("%.2f,%.2f" % (px, py))
        i = i + 1
    parts.append('<polyline fill="none" stroke="#1f77b4" stroke-width="1.50" points="%s"/>'
                 % " ".join(coords))
    parts.append('<text x="320.00" y="26.40" font-family="sans-serif" font-size="14" text-anchor="middle" fill="#222222">plot of %d points</text>'
                 % n)
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    n = int(sys.argv[1])
    svg = build(n)
    return len(svg) % 256


if __name__ == "__main__":
    sys.exit(main())
