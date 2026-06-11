# Packaged musl for ShivyC

This integration merges the musl source tree into ShivyC as importable Python
modules, so a program can be compiled against musl (bypassing glibc) **without
keeping thousands of `.h`/`.c` files in the ShivyC checkout**. The compiler
writes the files it needs to a temp directory at build time and sets the include
paths.

## What's included

```
shivyc/musl/
  __init__.py        # loader: materialize() + MuslTree (include flags, source extraction)
  _headers.py        # all 272 musl headers (public + internal) as raw strings
  _manifest.py       # category list + include-path layout
  <category>.py      # one module per src/<folder>: aio.py, complex.py, string.py, ...
tools/pack_musl.py   # regenerator: rebuilds shivyc/musl/ from a musl tree
```

Each `shivyc/musl/<category>.py` holds the folder's `.c` files as triple-quoted
raw strings:

```python
CATEGORY = 'string'
FILES = {
    'strlen.c': r'''#include <string.h>
size_t strlen(const char *s){ ... }''',
    ...
}
```

42 categories, 1538 source files, 272 headers. Every packaged file round-trips
**byte-for-byte** against the original musl tree (verified by the packager).

## How to use it

From the compiler driver (already wired by `main.py.patch`):

```
shivyc --musl  -c app.c -o app.o          # app.c #includes resolve to musl
shivyc --musl --musl-dir /tmp/m  -c app.c -o app.o
```

`--musl` materializes the musl headers and prepends their include dirs +
`-D_XOPEN_SOURCE=700` so `#include <...>` finds musl instead of glibc.

Programmatically:

```python
from shivyc import musl
tree = musl.materialize()                 # writes headers to /tmp/shivyc-musl-tree
user_cflags     = tree.public_cflags()    # -D... -I...  (for user code)
musl_src_cflags = tree.internal_cflags()  # adds musl's internal header dirs
src = tree.write_source("string", "strlen.c")   # extract one .c on demand
tree.write_category("ctype")              # or a whole category
```

Compile user code with `public_cflags()`, compile the musl `.c` you need with
`internal_cflags()`, then link statically with no glibc:

```
ld -static -nostdlib  start_tls.o crt_shivyc.o  app.o  <musl objs>  -o app
```

`build_musl_demo.sh` shows the full flow. The shipped demo compiles a program
using `strcpy/strlen/strcmp/memcpy` plus the musl sources those need
(`strcpy` pulls in `stpcpy` -- "compile only the parts the app requires"),
links with `ld -static -nostdlib`, and runs glibc-free
(`ldd` -> "not a dynamic executable").

## Regenerating after a musl change

```
python3 tools/pack_musl.py /path/to/shivyc-musl shivyc/musl
```

The packager prefers raw triple-quoted strings and falls back to `repr()` only
for the rare file containing the delimiter or ending in a backslash; it keys
headers by their path relative to the musl root and sources by name within each
category.

## Two patches required by this integration

* `main.py.patch` -- adds `--musl` / `--musl-dir` and applies the musl include
  paths/defines ahead of the user's.
* `token_kinds.py.patch` -- **NOTE:** the pulled HEAD was missing several
  keyword tokens that the rest of the tree already references
  (`volatile_kw`, `restrict_kw`, `atomic_kw`, `alignas_kw`, `typeof_kw`,
  `auto_type_kw`). This patch adds them. See the caveat below.

## Caveat: the pulled HEAD does not currently build

Independently of musl, the freshly pulled `brentharts/ShivyC` HEAD appears to be
an **incomplete commit**: several modules reference symbols that are not in the
pushed files. Adding the missing keyword tokens (above) clears the first wall,
but `shivyc/tree/general_nodes.py` then references
`shivyc.tree.decl_nodes.TypeofSpec`, which is not defined in the pushed
`decl_nodes.py` (the `typeof` / `__auto_type` / atomics / statement-expression
work looks only partially committed). The repo's own test suite fails ~478/527
as a result. These look like uncommitted local files rather than real design
gaps. Because reconstructing them by guessing would diverge from your local
work, this integration does **not** attempt to. The musl feature itself was
validated end-to-end with the last known-good compiler build.
