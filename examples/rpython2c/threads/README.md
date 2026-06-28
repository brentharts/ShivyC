# Register-partitioned bare-metal threads (rpython → ShivyCX contracts)

`threads_demo.py` is the rpython port of [`examples/threads_demo.c`](../../threads_demo.c):
two worker functions with disjoint, call-free bodies, declared as the `left` and
`right` halves of a two-way register partition.

```python
import rpy

@rpy.threads.left(core=0)
def foo() -> None:
    global la, lb, lc
    ...

@rpy.threads.right(core=0)
def bar() -> None:
    global ra, rb, rc
    ...

if __name__ == "__main__":
    rpy.threads.start_new_thread(foo)
    rpy.threads.start_new_thread(bar)
```

## What the decorators do

* **Under CPython** the decorators are identity wrappers (they tag the function
  with its `side`/`core` for introspection) and `rpy.threads.start_new_thread`
  spawns a real OS thread — so the same file runs, semi-faithfully, on the host.
* **Under py2c** the translator recognizes `@rpy.threads.left/right(core=N)`,
  strips it, and emits the ShivyCX register-partition contract in `main`'s header,
  guarded so gcc still accepts the C:

  ```c
  int main(void)
  #ifdef __SHIVYC__
  assert foo in threads.left(core=0)
  assert bar in threads.right(core=0)
  #endif
  { foo(); bar(); return 0; }
  ```

  Each `rpy.threads.start_new_thread(fn)` becomes a direct `fn()` call, and the
  `__main__` guard becomes `main`.

## The contract is real

`shivyc/thread_contracts.py` reads those contracts, scans each thread's actual
post-allocation register footprint, and partitions the register file so the two
threads share no registers — producing a *specialized* context switcher that
saves only each side's own registers (4 each way here, versus 22 for save-all).

The C generated from this rpython source produces a **byte-identical** switcher
(cooperative and preemptive) to the hand-written `threads_demo.c`:

```sh
# from the repo root
python3 -c "import sys;sys.path.insert(0,'tools');import py2c;\
py2c.write_runtime('/tmp/td');print(py2c.transpile_file(\
'examples/rpython2c/threads/threads_demo.py','/tmp/td'))"
python3 -m shivyc.main /tmp/td/threads_demo.c --emit-thread-switcher /tmp/sw.s -I/tmp/td
#   thread foo: side=left  core=0
#   thread bar: side=right core=0
#   context switch saves: left->right 4 regs, right->left 4 regs (vs 22 for save-all)
```

The worker bodies also compile and run under `--target arm64` (and gcc), so the
same source targets the host, x86-64, and AArch64.
