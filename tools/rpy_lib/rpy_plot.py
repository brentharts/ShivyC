"""rpy_plot -- a restricted-Python (rpython) port of `matplotlib-micropython`.

Ported from micropython's `lib/matplotlib-micropython/matplotlib` (the pure
-Python `_svg.py` / `_util.py` / `figure.py`) and retargeted at the rpython
dialect so py2c.py lowers the whole renderer to direct C. It produces the *same*
SVG bytes as the upstream module for the same inputs, so a transpiled plot can
be diffed against genuine matplotlib output. The module is plain Python too, so
it runs unchanged under CPython/PyPy3 as the correctness oracle.

Why this is structured the way it is (a single flat class over parallel arrays
rather than upstream's `Figure` -> `SvgRenderer` -> list-of-`line`-dicts):
ShivyCX lowers each class either to a bare C `struct` (POD) or to a boxed object
with a vtable. Three patterns force the slow/боxed path or miss-compile today, so
the port avoids all three by construction:

  * **No nested class-instance field.** Upstream's `Figure` holds an
    `SvgRenderer` *instance* in a field; a stored instance is dispatched through
    a vtable with boxed arguments. So `Figure` here is a single flat class -- it
    *is* the renderer, with no sub-object.
  * **No list of POD objects.** Upstream keeps each line as a `line` dict (and a
    typed port would use `list[Line]`); a list whose elements are class
    instances boxes them. Instead every per-line attribute lives in its own flat
    list (`_starts`, `_counts`, `_colors`, ...), and the point data of every
    line is concatenated into two shared coordinate lists (`_all_x`, `_all_y`)
    with per-line `(start, count)` slices -- the rpython "flat arrays + explicit
    sizes" idiom.
  * **No typed-list field, and no typed list across a method boundary.** A
    `list[float]` *field* and a `list[float]` *method argument* both miscompile
    across a module boundary today, so the list fields are left unannotated
    (boxed obj lists, which round-trip correctly) and `plot()` accepts boxed
    lists; elements are read back into annotated scalar locals at use.

Formatting uses printf-style `%` (py2c's well-tested `str_mod` path), which
keeps the emitted text byte-identical to upstream while staying on the fast
lowering. Free helper functions (`default_color`, `esc`, `axis_limits`) take
scalars / typed lists and lower to direct C.

API (the portable OO core of pyplot):
    default_color(index) -> str
    esc(text) -> str
    axis_limits(values, pad) -> [lo, hi]          (values: list[float])
    Figure(width, height)
        .plot(xs, ys, label, color, linewidth)    xs/ys: ordinary lists
        .set_title / .set_xlabel / .set_ylabel / .grid
        .to_svg() -> str
        .savefig(fname)
"""

def default_color(index: "int") -> "char*":
    # Matplotlib's default colour cycle, as an if-ladder rather than a
    # module-level list. A bundled library's module-level globals are only set
    # up by its `<module>_init()`, which the top-level program does not call
    # before `main()`, so a global list would still be empty at first use and
    # `index % len(list)` would divide by zero (SIGFPE). Indexing `% 10` against
    # a literal needs no initialization and allocates nothing.
    m = index % 10
    if m == 0:
        return "#1f77b4"
    if m == 1:
        return "#ff7f0e"
    if m == 2:
        return "#2ca02c"
    if m == 3:
        return "#d62728"
    if m == 4:
        return "#9467bd"
    if m == 5:
        return "#8c564b"
    if m == 6:
        return "#e377c2"
    if m == 7:
        return "#7f7f7f"
    if m == 8:
        return "#bcbd22"
    return "#17becf"


def esc(text: "char*") -> "char*":
    s = text
    s = s.replace("&", "&amp;")
    s = s.replace("<", "&lt;")
    s = s.replace(">", "&gt;")
    s = s.replace('"', "&quot;")
    return s


def _map_x(x: "float", x0: "float", x1: "float",
           px0: "float", px1: "float") -> "float":
    if x1 == x0:
        return (px0 + px1) * 0.5
    return px0 + (x - x0) * (px1 - px0) / (x1 - x0)


def _map_y(y: "float", y0: "float", y1: "float",
           py0: "float", py1: "float") -> "float":
    if y1 == y0:
        return (py0 + py1) * 0.5
    return py1 - (y - y0) * (py1 - py0) / (y1 - y0)


def axis_limits(values: "list[float]", pad: "float") -> "list[float]":
    # Return value is built by appending into a typed-list local rather than
    # returning a `[lo, hi]` literal: a list *literal* returned from a
    # `-> list[float]` function is built boxed and then pointer-cast to the
    # unboxed list type, which reads the doubles out of tagged-obj memory
    # (garbage). Appending into a `list[float]` local keeps it unboxed.
    res: "list[float]" = []
    n = len(values)
    if n == 0:
        res.append(0.0)
        res.append(1.0)
        return res
    # explicit min/max scan: the min()/max() builtins do not lower over an
    # unboxed `_tlist_double*` today, so reduce by hand.
    lo = values[0]
    hi = values[0]
    i = 1
    while i < n:
        v = values[i]
        if v < lo:
            lo = v
        if v > hi:
            hi = v
        i = i + 1
    if lo == hi:
        delta = 1.0
        if lo != 0.0:
            delta = abs(lo) * 0.1
        res.append(lo - delta)
        res.append(hi + delta)
        return res
    span = hi - lo
    res.append(lo - span * pad)
    res.append(hi + span * pad)
    return res


class Figure:
    """A flat SVG line-chart: the figure and its renderer in one POD-friendly
    class, with per-line data held in parallel boxed lists."""

    def __init__(self, width: "int", height: "int"):
        self.width = width
        self.height = height
        self.margin = 48
        self.title = ""
        self.xlabel = ""
        self.ylabel = ""
        self.show_grid = False
        self.line_count = 0
        # flat per-line arrays (boxed obj lists; read back into scalar locals)
        self._all_x = []          # concatenated x of every line
        self._all_y = []          # concatenated y of every line
        self._starts = []         # per-line start index into _all_x / _all_y
        self._counts = []         # per-line point count
        self._colors = []         # per-line stroke colour
        self._labels = []         # per-line legend label ("" = none)
        self._widths = []         # per-line stroke width

    def plot(self, xs, ys, label: "char*", color: "char*",
             linewidth: "float") -> None:
        if len(color) == 0:
            color = default_color(self.line_count)
        self.line_count = self.line_count + 1
        self._starts.append(len(self._all_x))
        self._counts.append(len(xs))
        self._colors.append(color)
        self._labels.append(label)
        self._widths.append(linewidth)
        i = 0
        while i < len(xs):
            xv: "float" = xs[i]
            yv: "float" = ys[i]
            self._all_x.append(xv)
            self._all_y.append(yv)
            i = i + 1

    def set_title(self, title: "char*") -> None:
        self.title = title

    def set_xlabel(self, xlabel: "char*") -> None:
        self.xlabel = xlabel

    def set_ylabel(self, ylabel: "char*") -> None:
        self.ylabel = ylabel

    def grid(self, visible: "bool") -> None:
        self.show_grid = visible

    def to_svg(self) -> "char*":
        # Build local typed value lists from the boxed per-point fields, then
        # reduce to axis bounds via the free `axis_limits`. Done as locals (not
        # a list-returning method) because a method declared to return
        # `list[float]` is reported to callers as a boxed obj today.
        xvals: "list[float]" = []
        yvals: "list[float]" = []
        p = 0
        while p < len(self._all_x):
            ax: "float" = self._all_x[p]
            ay: "float" = self._all_y[p]
            xvals.append(ax)
            yvals.append(ay)
            p = p + 1
        xlim = axis_limits(xvals, 0.05)
        ylim = axis_limits(yvals, 0.05)
        xlo = xlim[0]
        xhi = xlim[1]
        ylo = ylim[0]
        yhi = ylim[1]

        pl = float(self.margin)
        pr = float(self.width - self.margin)
        pt = float(self.margin)
        pb = float(self.height - self.margin)

        parts: "list[char*]" = []
        parts.append('<?xml version="1.0" encoding="UTF-8"?>')
        parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">'
                     % (self.width, self.height))
        parts.append('<rect width="100%" height="100%" fill="white"/>')

        if self.show_grid:
            i = 0
            while i < 6:
                t = i / 5.0
                gx = pl + t * (pr - pl)
                gy = pt + t * (pb - pt)
                parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#dddddd" stroke-width="1"/>'
                             % (gx, pt, gx, pb))
                parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#dddddd" stroke-width="1"/>'
                             % (pl, gy, pr, gy))
                i = i + 1

        parts.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" fill="none" stroke="#222222" stroke-width="1"/>'
                     % (pl, pt, pr - pl, pb - pt))

        li = 0
        while li < len(self._starts):
            st: "int" = self._starts[li]
            cn: "int" = self._counts[li]
            color: "char*" = self._colors[li]
            lw: "float" = self._widths[li]
            li = li + 1
            if cn < 2:
                continue
            coords: "list[char*]" = []
            k = 0
            while k < cn:
                xv: "float" = self._all_x[st + k]
                yv: "float" = self._all_y[st + k]
                px = _map_x(xv, xlo, xhi, pl, pr)
                py = _map_y(yv, ylo, yhi, pt, pb)
                coords.append("%.2f,%.2f" % (px, py))
                k = k + 1
            parts.append('<polyline fill="none" stroke="%s" stroke-width="%.2f" points="%s"/>'
                         % (color, lw, " ".join(coords)))

        if len(self.title) > 0:
            parts.append('<text x="%.2f" y="%.2f" font-family="sans-serif" font-size="14" text-anchor="middle" fill="#222222">%s</text>'
                         % (self.width * 0.5, self.margin * 0.55, esc(self.title)))

        if len(self.xlabel) > 0:
            parts.append('<text x="%.2f" y="%.2f" font-family="sans-serif" font-size="12" text-anchor="middle" fill="#222222">%s</text>'
                         % (self.width * 0.5, self.height - self.margin * 0.25, esc(self.xlabel)))

        if len(self.ylabel) > 0:
            parts.append('<text x="%.2f" y="%.2f" font-family="sans-serif" font-size="12" text-anchor="middle" fill="#222222" transform="rotate(-90 %.2f %.2f)">%s</text>'
                         % (self.margin * 0.35, self.height * 0.5,
                            self.margin * 0.35, self.height * 0.5, esc(self.ylabel)))

        # legend: one entry per line that carries a non-empty label, in order
        lx = pr - 8.0
        ly = pt + 16.0
        slot = 0
        li = 0
        while li < len(self._labels):
            label: "char*" = self._labels[li]
            lcolor: "char*" = self._colors[li]
            li = li + 1
            if len(label) == 0:
                continue
            y = ly + slot * 16.0
            slot = slot + 1
            parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="%s" stroke-width="2"/>'
                         % (lx - 28.0, y, lx - 8.0, y, lcolor))
            parts.append('<text x="%.2f" y="%.2f" font-family="sans-serif" font-size="11" text-anchor="start" fill="#222222">%s</text>'
                         % (lx - 4.0, y + 4.0, esc(label)))

        parts.append("</svg>")
        return "\n".join(parts)

    def savefig(self, fname: "char*") -> None:
        svg = self.to_svg()
        f = open(fname, "w")
        f.write(svg)
        f.close()
