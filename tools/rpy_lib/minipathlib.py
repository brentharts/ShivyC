# Minimal `pathlib.Path` shim for the minipy subset: a path is a plain string.
# Supports what py2c uses -- construction, `/` joining (__truediv__), resolve()
# (a no-op; minipy does no filesystem access), str(), and the parents / stem /
# name / parts / suffix accessors (computed eagerly, since the subset has no
# @property).


def _split_nonempty(s):
    out = []
    for seg in s.split("/"):
        if seg != "":
            out.append(seg)
    return out


def _norm(p):
    if p == "":
        return ""
    lead = ""
    if p[0:1] == "/":
        lead = "/"
    return lead + "/".join(_split_nonempty(p))


def _stem_of(base):
    if "." in base and base[0:1] != ".":
        segs = base.split(".")
        return ".".join(segs[0:len(segs) - 1])
    return base


class Path:
    def __init__(self, p=""):
        s = _norm(p)
        self.s = s
        comps = _split_nonempty(s)
        self.parts = tuple(comps)
        base = ""
        if len(comps) > 0:
            base = comps[len(comps) - 1]
        self.name = base
        self.stem = _stem_of(base)
        lead = ""
        if s[0:1] == "/":
            lead = "/"
        pars = []
        i = len(comps) - 1
        while i > 0:
            pars.append(Path(lead + "/".join(comps[0:i])))
            i = i - 1
        if len(comps) > 0:
            if lead == "/":
                pars.append(Path("/"))        # absolute paths bottom out at root
            else:
                pars.append(Path("."))        # relative paths at the current dir
        self.parents = pars                   # root "/" itself has no parents

    def resolve(self):
        return self

    def absolute(self):
        return self

    def __truediv__(self, other):
        if self.s == "":
            return Path(other)
        return Path(self.s + "/" + other)

    def __str__(self):
        return self.s
