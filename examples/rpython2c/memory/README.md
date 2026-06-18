# rpython memory management — `del`, auto-free, and the `--pdf` report

Two examples show ShivyCX's manual + compiler-assisted memory model (no GC, no
refcounting). The whole-program analysis lives in `shivyc/memory_safety.py`.

## `del_demo.py` — explicit `del`
`del` lowers to the right deallocator for what is freed:

| target                                   | lowering              |
|------------------------------------------|-----------------------|
| libc-`malloc`'d buffer / POD instance    | `free(p)`             |
| arena (object-model) instance            | `afree(p, sizeof *p)` |
| `del d[k]` on a dict/list                | `del_item(d, k)`      |
| borrowed scalar (`char*`, `int`, …)      | no-op                 |

```
python3 -m shivyc.main --no-cache del_demo.py -o /tmp/del && /tmp/del   # exit 60
```

## `autofree.py` — the compiler inserts `free()` for you
Because ShivyCX sees the whole call graph, an escape analysis proves which
allocations never outlive the function that made them and inserts a `free()` at
that function's exit. A returned (escaping) allocation is left for its caller.

```
# report the analysis (no code change):
python3 -m shivyc.main --no-cache --check-memory --auto-free autofree.py -o /tmp/af
#   -> "in scratch_sum: 1 allocation(s) the compiler can free at function exit"

# actually insert the frees and build:
python3 -m shivyc.main --no-cache --auto-free autofree.py -o /tmp/af && /tmp/af   # exit 135
#   -> "auto-free: inserted 1 free(s) for non-escaping allocations"
```

## `--pdf`: memory findings in the build report
`--pdf` renders the analysis into the report — the safety verdict (any
use-after-free / double-free in red) and the auto-free candidates per function,
beside the rpython source and generated C:

```
python3 -m shivyc.main --no-cache --pdf report autofree.py -o /tmp/af
#   report/shivyc_report.pdf  ->  "Auto-free candidates ... scratch_sum: 1 allocation(s)"
```
