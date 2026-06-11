# Memory safety for unannotated C

C's manual `malloc`/`free` is the classic source of **use-after-free** and
**double-free** bugs. Because ShivyCX sees the entire call graph, a Python pass
(`shivyc/memory_safety.py`) tracks every allocation, pointer copy (alias), and
free across the whole program and:

* **flags use-after-free** — a dereference (or pass to a callee that
  dereferences) of a pointer whose allocation has already been freed, *including
  through aliases and across function boundaries*;
* **flags double-free** — freeing an allocation that is already freed;
* **auto-frees** — when escape/region analysis proves an allocation is local
  with no remaining live reference, the compiler can insert the `free` for you,
  so the programmer may omit it.

This recovers much of Rust's ownership safety for ordinary C, driven by
whole-program reachability rather than by annotations — without Rust's wholesale
change of language.

## Usage

Report only (no code generated):

```
python3 -m shivyc.main examples/memory/dangling_alias.c --check-memory
```

Insert automatic frees during a normal compile:

```
python3 -m shivyc.main examples/memory/autofree_leak.c --auto-free -o leak
```

`--check-memory --auto-free` additionally lists the auto-free candidates without
modifying anything. From the repo root, `make check-memory` runs all four
examples.

## The examples

| file | what it shows | result |
|------|---------------|--------|
| `dangling_alias.c` | the canonical alias-outlives-free bug from the brief | use-after-free |
| `double_free.c` | the same allocation freed twice | double-free |
| `wrapper_uaf.c` | free in one helper, deref in another (whole-program) | use-after-free |
| `autofree_leak.c` | a leak with no escaping reference | auto-free inserts `free` |

## How it works

The pass runs on ShivyCX's IL — the same `Call.direct_name` / `Set` (alias) /
`ReadAt` & `SetAt` (dereference) commands the other whole-program analyses use,
so it sees aliasing as it actually flows through the generated code.

1. **Per-function dataflow.** A CFG is built from each function's IL. A
   forward, flow-sensitive analysis tracks a *may-points-to* map
   (pointer → set of abstract allocations) and each allocation's state
   (`allocated` / `maybe-freed` / `freed`), merging at control-flow joins.
   `malloc`/`calloc`/`strdup`/… create allocations; `free`/`kfree` free them;
   `Set` propagates aliases; `ReadAt`/`SetAt` are the use sites checked against
   the freed state.

2. **Whole-program summaries.** Functions are analyzed callees-first. Each gets
   a summary — *frees parameter i*, *dereferences parameter i*, *parameter i
   escapes*, *returns an owned allocation* — which is applied at call sites. This
   is what catches the `wrapper_uaf.c` bug and what avoids false positives when a
   function returns ownership (e.g. a `malloc` wrapper).

3. **Escape/region analysis → auto-free.** An allocation is auto-freeable when
   it is created locally and provably never escapes (not returned, not stored
   into a global or through a pointer, not passed to an escaping call) and is
   never already freed on any path. Such allocations are dead at function exit,
   so a `free` is inserted before each `return`.

## Honest limitations

* The analysis is flow-sensitive within a function and **summary-based** across
  functions; it is not fully field- or path-sensitive. Heap-to-heap aliasing
  through stored pointers is treated conservatively (as escape), which favors
  soundness of auto-free (never free something that might be live) over
  completeness (some real leaks are left alone).
* Diagnostics are reported at function granularity (the IL carries no source
  line numbers), naming the function and the kind of error.
* Auto-free **insertion** reuses a `free` reference already present in the
  translation unit; if a unit never frees anything, candidates are still
  reported but not inserted (there is no deallocator symbol to call).
* Conservative by construction: when the analysis cannot prove an allocation is
  dead, it does nothing — it never inserts a free that could create a
  double-free or free an escaping pointer.
