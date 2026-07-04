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

    def normpath(self, p):
        # pure-string path normalization: collapse '', '.', and '..' segments.
        if p == "":
            return "."
        rooted = p[0] == "/"
        out = []
        for seg in p.split("/"):
            if seg == "" or seg == ".":
                continue
            if seg == "..":
                if len(out) > 0 and out[len(out) - 1] != "..":
                    out.pop()
                elif not rooted:
                    out.append(seg)
            else:
                out.append(seg)
        res = "/".join(out)
        if rooted:
            res = "/" + res
        if res == "":
            return "/" if rooted else "."
        return res

    def relpath(self, p, start=None):
        if start is None:
            start = "."
        pa = self.normpath(p).split("/")
        sa = self.normpath(start).split("/")
        i = 0
        while i < len(pa) and i < len(sa) and pa[i] == sa[i]:
            i = i + 1
        ups = []
        k = i
        while k < len(sa):
            ups.append("..")
            k = k + 1
        rest = pa[i:len(pa)]
        parts = ups + rest
        if len(parts) == 0:
            return "."
        return "/".join(parts)

    def commonpath(self, paths):
        if len(paths) == 0:
            return ""
        split = []
        for p in paths:
            split.append(self.normpath(p).split("/"))
        first = split[0]
        i = 0
        common = []
        while i < len(first):
            seg = first[i]
            same = True
            for parts in split:
                if i >= len(parts) or parts[i] != seg:
                    same = False
            if not same:
                break
            common.append(seg)
            i = i + 1
        return "/".join(common)

    # No filesystem view on native minipy: predicates report absent. On the
    # reference VM the injected host os answers them accurately.
    def exists(self, p):
        if _host_os is not None:
            return _host_os.path.exists(p)
        return False

    def isfile(self, p):
        if _host_os is not None:
            return _host_os.path.isfile(p)
        return False

    def isdir(self, p):
        if _host_os is not None:
            return _host_os.path.isdir(p)
        return False


path = _Path()


# minipy runs with no real environment. The transpiler only reads *optional*
# feature-flag env vars (PY2C_*, RPY_PROFILE_*), so an environment that reports
# every variable as unset makes it take the normal (feature-off) path -- exactly
# what a clean, reproducible self-transpile wants.
class _Environ:
    def get(self, key, default=None):
        return default

    def __contains__(self, key):
        return False


environ = _Environ()


def getenv(key, default=None):
    return default


# minipy runs without a real filesystem. The transpiler's cross-file scans (used
# to resolve symbol collisions across a package) therefore see no sibling files,
# which is correct for a single self-contained source: these return empty.
#
# On the reference VM (CPython-hosted), the interpreter injects the real `os`
# module into the module-global `_host_os` (which is never assigned here, so the
# injection survives module load). Native minipy leaves it unset (None), keeping
# the no-filesystem behaviour. This lets a self-hosted transpile actually read
# inputs and write its C outputs when run on the reference VM.
def listdir(p):
    if _host_os is not None:
        return _host_os.listdir(p)
    return []


def walk(top):
    if _host_os is not None:
        return list(_host_os.walk(top))
    return []                                 # list of (dir, subdirs, files)


def makedirs(p, exist_ok=False):
    if _host_os is not None:
        _host_os.makedirs(p, exist_ok=exist_ok)
    return None


def remove(p):
    if _host_os is not None:
        _host_os.remove(p)
    return None


def fspath(p):
    return p

