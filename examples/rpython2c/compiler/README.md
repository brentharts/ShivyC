# Rewriting compiler hotspots in rpython — a lexer kernel

This is the first step of moving ShivyCX's own hot paths into the rpython subset
so they translate to fast C. Lexing touches every source character, so it is the
classic compiler hotspot.

`lexer_kernel.py` is a C-subset scanner: it skips whitespace and `//`+`/* */`
comments and classifies every token (identifier/keyword, number, string, char,
operator/punctuation), folding each token's kind, length and a byte-hash of its
text into a rolling checksum. It is written so the **same file**:

- runs as plain Python — the reference implementation, and
- transpiles via `tools/py2c.py` to the identical logic as native C.

## What makes it translate to tight C

- `ord(s[i])` on a `char*` compiles to a direct byte read — no per-character
  string allocation. (The inner loop has zero allocations: no `char_at`,
  `pyord`, `list_of`, or `obj_*` calls.)
- The keyword and symbol tables are typed `list[int]` (unboxed), not the tagged
  object model.
- Hash accumulators are annotated `"i64"` so the `h * 131` arithmetic stays
  64-bit; all values are kept within 31 bits so Python's big integers and C's
  `long` agree **exactly** — the checksum is identical across all three
  backends, byte for byte.

## Benchmark

```
python3 examples/rpython2c/compiler/bench.py 20000
```

```
backend       time(s)  speedup   result
CPython         4.45      1.0x    reference
ShivyCX         0.24     18.6x    ok          <- ShivyCX's own C backend
gcc -O2         0.08     53.5x    ok
```

All three produce the same checksum (`result: ok`), so the rewrite is faithful;
the rpython version runs ~18x faster through ShivyCX and ~50x faster through gcc.

## Why this matters

The compiler's own lexer/parser/codegen are pure, character- and table-driven
passes — exactly the shape that benefits. Writing them in rpython lets the same
source stay debuggable in Python while compiling to native speed, and keeps them
within the C-subset ShivyCX can self-host. This kernel establishes the pattern
and the enabling primitives (fast byte access, 64-bit integer arithmetic, typed
container tables) for migrating real ShivyCX passes next.
