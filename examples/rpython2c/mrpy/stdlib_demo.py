# Run me with mrpy. Demonstrates that the embedded MicroPython has its internal
# stdlib compiled in and importable: sys, math, collections, struct, array.
import sys
import math
import collections
import struct
import array

print("platform:", sys.platform)
print("sqrt(2):", math.sqrt(2))

od = collections.OrderedDict()
od["one"] = 1
od["two"] = 2
print("ordered keys:", list(od.keys()))

packed = struct.pack("<3i", 10, 20, 30)
print("struct round-trip:", list(struct.unpack("<3i", packed)))

a = array.array("i", [4, 5, 6])
print("array sum:", sum(a))
