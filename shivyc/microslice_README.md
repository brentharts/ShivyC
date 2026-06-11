# Micro-slicing: productive spinning

A core that loses a lock race normally burns its cycles polling. The
micro-slicing pass (paper §7.1) fills that spin window with **useful,
independent work** instead, without delaying entry to the critical section by
more than one slice.

The idea has three parts, all driven by the whole-program call graph and
implemented in [`shivyc/microslice.py`](../../shivyc/microslice.py):

1. **Candidate identification.** Find a fragment that is safe to interleave with
   a spin-wait: it must be **pure** (no writes to shared memory, no impure
   calls), and therefore **memory-independent** of whatever the held lock
   protects. A pure value-returning loop (`frag_ref` in the demo) qualifies;
   anything that stores through a pointer or calls `printf`/`malloc`/… does not.
2. **The fragmentor.** Estimate the cost of the fragment's hot loop by counting
   IL operations, then quantize it into slices of a bounded budget. If a slice
   costs at most `T`, the lock is polled at least every `T`, so the added
   acquisition latency is bounded by `T`.
3. **Code generation.** Replace the idle acquire with a loop that runs one slice
   of the fragment per poll.

## Run the analysis

```
python3 -m shivyc.main examples/microslice/productive_spin.c --microslice
```

```
interleavable fragments (pure + bounded loop):
  frag_ref(): pure, hot loop over 2 block(s), ~12 cost/iteration
      -> slice = 5 iteration(s) per poll (~20 ns); lock observed at least every slice
      -> added acquisition latency bounded by ~20 ns (one slice)

not interleavable (has side effects):
  frag_slice_step(): writes through a pointer (store to caller memory)
  try_acquire(): writes through a pointer (store to caller memory)
  ...
```

The per-slice budget is tunable; it sets the slice size from the measured
per-iteration cost:

```
python3 -m shivyc.main examples/microslice/productive_spin.c --microslice --slice-budget 96
#   -> slice = 8 iteration(s) per poll (~32 ns)
```

And the code-generation step can emit the work-injected acquire scaffold:

```
python3 -m shivyc.main examples/microslice/productive_spin.c \
    --microslice --slice-budget 96 --emit-microslice acquire.c
```

## Run the demo

```
python3 -m shivyc.main examples/microslice/productive_spin.c -o spin && ./spin
```

```
idle spin       : 4000 polls, 0 useful iterations (all wasted)
productive spin : 4000 polls, 20000 useful iterations during the spin
fragment result : correct (acc=2666466670000, ref=2666466670000)
```

Both runs spin the same number of times waiting for the lock. The idle version
throws every poll away; the productive version finishes all 20000 iterations of
the independent fragment *during the wait*, and the result matches the reference
computation exactly. `make microslice` runs both the analysis and the demo.

## How the demo models contention

`try_acquire` ticks down a `held` counter that stands in for the cycles the
*other* core holds the critical section; on real hardware this is a
`test_and_set` on a shared word released by another core. The interleaving
mechanism — run a bounded slice of independent work between polls — is identical
either way, and is exactly what the generated `acquire_productive` does.

## Honest limitations

* The slicing is demonstrated at *iteration* granularity on a counted loop,
  where the live state to carry across a slice boundary is small and explicit
  (an index and an accumulator). Slicing arbitrary control flow would require
  general state capture (continuations); the analysis here targets the common
  bounded-loop case the paper describes.
* The cost model is a static per-operation estimate used only to *size* slices;
  it is not a cycle-accurate WCET. The latency bound it reports is in those same
  estimated units (quoted as ns at a nominal 3 GHz).
* Purity is the strong, sound form — no writes to caller-visible memory and no
  impure calls. This may reject fragments that are in fact independent of a
  particular lock's data; tightening it to a per-lock independence test is
  future work.
