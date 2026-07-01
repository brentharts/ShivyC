# strbuild.py -- the growing-accumulator string-building pattern.
#
# This is the shape of a code generator's output buffer: `out = out + chunk`
# repeated as the string grows (exactly minipy2c.py's `self.out = self.out + s`).
# CPython special-cases `s = s + t` on a uniquely-referenced str with an in-place
# realloc, so it stays ~O(n); a naive tagged-value interpreter reallocates and
# copies the whole accumulator each step, paying O(n^2) per built string. Each
# round rebuilds a bounded-length string (so the no-GC bump arena stays small)
# and the outer loop runs it thousands of times, so the hot concat path
# dominates. Output is a content-sensitive checksum for differential correctness.
CHUNKS = ["def ", "foo", "(x)", ": ", "return ", "x + 1", "\n", "    "]

rounds = 0
checksum = 0
while rounds < 3000:
    s = ""
    j = 0
    while j < 80:
        s = s + CHUNKS[j % 8]
        j = j + 1
    checksum = checksum + len(s) + s.find("return")
    rounds = rounds + 1
print(checksum)
