import os

# Under the 3-way harness cpython runs the real `os.path` while the minipy ref
# VM and native build run the compile-time-linked `minios`. Identical output
# across all three checks both that module linking handles nested attribute
# access (os.path.*) and module-level instances, and that minios matches
# CPython's posixpath on the inputs py2c produces.

print("sep=" + os.sep)

_joins = [("a", "b"), ("a/", "b"), ("a", "/b"), ("", "b"), ("dir", "sub")]
i = 0
while i < len(_joins):
    pair = _joins[i]
    print("join " + pair[0] + " + " + pair[1] + " = " + os.path.join(pair[0], pair[1]))
    i = i + 1

print("join3=" + os.path.join("r", "s", "t"))

_paths = ["a/b/c.py", "/x", "file", "/", "d/e/", "pkg/mod.tar.gz",
          "dir/.hidden", "noext"]
j = 0
while j < len(_paths):
    p = _paths[j]
    r, e = os.path.splitext(p)
    print("p=" + p + " dir=" + os.path.dirname(p) + " base=" + os.path.basename(p)
          + " root=" + r + " ext=[" + e + "]")
    j = j + 1
