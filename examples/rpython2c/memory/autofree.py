"""Whole-program auto-free -- the compiler inserts `free()` for you.

Because ShivyCX sees the entire call graph, a static escape analysis can prove
that some heap allocations never outlive the function that made them. With
`--auto-free` it inserts a `free()` for each such allocation at the owning
function's exit -- so the source can omit it, with no leak and no runtime/GC.

`scratch_sum` allocates a scratch buffer it never returns -> the allocation is
local and non-escaping, so the compiler frees it. `build` (in the companion
note) would *return* its buffer, so it escapes and is left alone.

    # see the analysis (no code change):
    python3 -m shivyc.main --no-cache --check-memory --auto-free autofree.py -o /tmp/af
    # actually insert the frees and build:
    python3 -m shivyc.main --no-cache --auto-free autofree.py -o /tmp/af && /tmp/af
    # full report (source, generated C, call graph, memory findings) as a PDF:
    python3 -m shivyc.main --no-cache --pdf report --auto-free autofree.py -o /tmp/af
"""


def scratch_sum(n: "int") -> int:
    buf: "i32*" = malloc(n * 4)      # non-escaping: auto-freed at return
    i = 0
    while i < n:
        buf[i] = i
        i = i + 1
    s = 0
    i = 0
    while i < n:
        s = s + buf[i]
        i = i + 1
    return s                          # buf not returned -> compiler frees it


def main() -> int:
    total = 0
    r = 0
    while r < 3:
        total = total + scratch_sum(10)     # 45 each
        r = r + 1
    return total % 256                       # 135
