"""`del` in rpython -- manual memory management, no runtime, no GC.

ShivyCX lowers `del` to the right deallocator for what is being deleted:

  * a libc-`malloc`'d buffer or POD class instance  ->  `free(p)`
  * an arena-allocated (object-model) instance       ->  `afree(p, size)`
  * `del d[k]` on a dict/list                         ->  `del_item(d, k)`
  * a borrowed scalar (char*, int, ...)               ->  no-op

Here a typed array and a POD class instance are both released with `del`.

    python3 -m shivyc.main --no-cache del_demo.py -o /tmp/del && /tmp/del
"""


class Accumulator:
    def __init__(self, base: "int"):
        self.base = base
        self.total = base

    def add(self, x: "int") -> None:
        self.total = self.total + x


def main() -> int:
    # A heap buffer, explicitly released with `del` -> free(buf).
    buf: "i32*" = malloc(16 * 4)
    i = 0
    while i < 16:
        buf[i] = i * i
        i = i + 1
    s = 0
    i = 0
    while i < 16:
        s = s + buf[i]
        i = i + 1
    del buf                          # free(buf)

    # A POD class instance, also released with `del` -> free(acc).
    acc = Accumulator(100)
    acc.add(s)
    out = acc.total
    del acc                          # free(acc)

    return out % 256                 # 100 + (0+1+4+...+225) = 100 + 1240 = 1340 -> 60
