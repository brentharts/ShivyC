# minios: the subset of the `os` module that py2c.py needs and that can be
# implemented as pure string manipulation -- `os.sep` and the POSIX
# `os.path.{join,split,dirname,basename,splitext}` functions. Their results
# match CPython's posixpath on the inputs py2c produces (checked differentially
# in the tests). Filesystem-touching members (abspath/isdir/isfile/exists/walk/
# listdir, and the normpath/relpath/commonpath path algebra) are intentionally
# left out for now: they need real I/O or more involved logic and, in py2c, live
# mostly in the host-CPython file driver.
#
# Written in the minipy subset (classes, while/for, tuples, slicing -- no
# comprehensions, imports, or str.rfind/rstrip, which minipy lacks) so the same
# source runs on CPython and, once linked, on minipy.

sep = "/"


def _rfind(s, ch):
    i = len(s) - 1
    while i >= 0:
        if s[i] == ch:
            return i
        i = i - 1
    return -1


def _rstrip_slash(s):
    i = len(s)
    while i > 0 and s[i - 1] == "/":
        i = i - 1
    return s[0:i]


def _join2(a, b):
    # posixpath's two-argument join: an absolute b wins; otherwise glue with a
    # single separator unless a already ends in one (or is empty).
    if len(b) > 0 and b[0] == "/":
        return b
    if len(a) == 0 or a[len(a) - 1] == "/":
        return a + b
    return a + "/" + b


class _Path:
    sep = "/"

    def join(self, a, b=None, c=None, d=None):
        # variadic join is unavailable (minipy methods take fixed params), but
        # py2c only ever passes 1-4 components, folded left here.
        r = a
        if b is not None:
            r = _join2(r, b)
        if c is not None:
            r = _join2(r, c)
        if d is not None:
            r = _join2(r, d)
        return r

    def split(self, p):
        i = _rfind(p, "/") + 1
        head = p[0:i]
        tail = p[i:len(p)]
        if len(head) > 0:
            allslash = 1
            k = 0
            while k < len(head):
                if head[k] != "/":
                    allslash = 0
                k = k + 1
            if allslash == 0:
                head = _rstrip_slash(head)
        return (head, tail)

    def dirname(self, p):
        h, t = self.split(p)
        return h

    def basename(self, p):
        h, t = self.split(p)
        return t

    def abspath(self, p):
        # minipy has no cwd; paths handed to a self-hosted run are already
        # absolute (e.g. __file__), so return them unchanged, and only anchor a
        # clearly-relative path at "/" as a best effort.
        if len(p) > 0 and p[0] == "/":
            return p
        return "/" + p

    def splitext(self, p):
        sep_i = _rfind(p, "/")
        dot_i = _rfind(p, ".")
        if dot_i > sep_i:
            fstart = sep_i + 1
            i = fstart
            while i < len(p) and p[i] == ".":
                i = i + 1
            if dot_i >= i:
                return (p[0:dot_i], p[dot_i:len(p)])
        return (p, "")


path = _Path()
