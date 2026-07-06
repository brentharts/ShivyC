"""Binary bytecode container for minipy: the `.mpyc` format.

Motivation
----------
minipy's interpreter consumes the *flattened, pre-optimised* bytecode produced by
`minipy.compiler.compile_file`. Historically that bytecode was serialised as
JSON and re-parsed on every run. JSON is doubly expensive at load time: the text
must be tokenised, and the generic `object_hook` rebuilds every record by name
into the interpreter's POD structs. For a large program (py2c.py itself compiles
to tens of thousands of instructions) this dominates a short run and pollutes any
attempt to benchmark *interpreter* speed against CPython's `python3 x.pyc`.

`.mpyc` is a compact, positional binary encoding of the exact same `Program`
struct (see minipy/interp.py). There are no field names in the stream and no
intermediate dictionaries: the loader walks the bytes with a single cursor and
fills the POD structs directly, so loading is a linear memcpy-ish pass.

Format (version 1), little-endian, fully positional
---------------------------------------------------
    magic        4 bytes  "MPYC"
    version      uvarint  (== 1)
    source       str
    consts       uvarint count, then per const:  str t, svarint i, str d, str s
    names        uvarint count, then per name:    str
    nglobals     uvarint
    funcs        uvarint count, then per func:
                     str name
                     uvarint nparams, nregs, nlocals
                     uvarint ncode, then per instr:
                         byte op, uvarint ra, byte fb, byte fc, svarint b, svarint c
                     uvarint ndefaults, then per default: svarint
                     svarint vararg
                     uvarint nparam-names, then per name: str
    classes      uvarint count, then per class:
                     str cname, svarint base, uvarint nmethods,
                     then per method: str mname, svarint mfunc
    entry        uvarint

Primitives
    uvarint : unsigned LEB128 (7 data bits/byte, high bit = continuation).
    svarint : zig-zag mapped to uvarint, so small magnitudes stay one byte.
    str     : uvarint byte-length, then that many raw UTF-8 bytes. A None value
              is encoded as length 0 (indistinguishable from ""); no minipy
              string field is legitimately None, so this is safe.
    double  : stored as its round-trippable repr() *string* rather than raw IEEE
              bytes. This keeps the loader identical on CPython and on the
              py2c-compiled interpreter (whose struct subset reinterprets an int
              bit-pattern, not a byte buffer) and dodges NUL-in-buffer issues.

Embedded NUL bytes in the stream are fine for the interpreter's loader: it reads
`ord(buf[cursor])` at tracked offsets from the length prefixes and never calls
`len()` on the whole buffer, so a 0x00 varint byte is read correctly.

This module (the *encoder*) runs only under CPython, as the ahead-of-time
"compile to .mpyc" step. The interpreter reimplements the decode in
py2c-compatible style; `decode()` here is a reference implementation used to
round-trip-test the format.
"""

MAGIC = b"MPYC"
VERSION = 1


# --------------------------------------------------------------------------- #
# encoder                                                                      #
# --------------------------------------------------------------------------- #
def _w_uvarint(out, n):
    # NUL-free LEB128: the stream must contain no 0x00 byte, because the
    # interpreter reads the file into a NUL-terminated char* whose length is
    # strlen() -- an embedded NUL would truncate it and break slicing. Standard
    # LEB128 emits 0x00 only for the value 0; biasing every value by +1 makes
    # the final (non-continuation) byte's 7-bit group always >= 1, and
    # continuation bytes always have bit 7 set, so no byte is ever 0x00.
    if n < 0:
        raise ValueError("uvarint got negative value %r" % (n,))
    n += 1
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return


def _w_svarint(out, n):
    # zig-zag: 0,-1,1,-2,2 -> 0,1,2,3,4 (portable for any Python int)
    _w_uvarint(out, (n << 1) if n >= 0 else (((-n) << 1) - 1))


def _w_str(out, s):
    if s is None:
        _w_uvarint(out, 0)
        return
    data = s.encode("utf-8")
    _w_uvarint(out, len(data))
    out.extend(data)


def _w_double(out, d):
    _w_str(out, repr(float(d)))


def encode(prog):
    """Serialise a `compile_file`/`to_program` dict to `.mpyc` bytes."""
    out = bytearray()
    out.extend(MAGIC)
    _w_uvarint(out, VERSION)
    _w_str(out, prog.get("source", "<module>"))

    consts = prog["consts"]
    _w_uvarint(out, len(consts))
    for c in consts:
        # Positional but type-tagged: only the slot this const actually uses is
        # written, so the common int/str consts cost one field, not four, and
        # the loader does no wasted double-parse / string alloc per const.
        t = c["t"]
        _w_str(out, t)
        if t == "float":
            _w_double(out, c.get("d", 0.0))
        elif t == "str":
            _w_str(out, c.get("s", ""))
        elif t == "none":
            pass
        else:  # int, bool, func, builtin, class -> integer payload in i
            _w_svarint(out, int(c.get("i", 0)))

    names = prog["names"]
    _w_uvarint(out, len(names))
    for nm in names:
        _w_str(out, nm)

    _w_uvarint(out, prog["nglobals"])

    funcs = prog["funcs"]
    _w_uvarint(out, len(funcs))
    for f in funcs:
        _w_str(out, f["name"])
        _w_uvarint(out, f["nparams"])
        _w_uvarint(out, f["nregs"])
        _w_uvarint(out, f["nlocals"])
        code = f["code"]
        _w_uvarint(out, len(code))
        for ins in code:
            # pack opcode + the two 1-bit free-reg hints into one uvarint so no
            # raw 0x00 byte can appear (op 0 / fb 0 / fc 0 would otherwise be a
            # literal NUL): (op<<2)|(fb<<1)|fc
            _w_uvarint(out, ((ins["op"] & 0xFF) << 2)
                       | ((1 if ins["fb"] else 0) << 1)
                       | (1 if ins["fc"] else 0))
            _w_uvarint(out, ins["ra"])
            _w_svarint(out, ins["b"])
            _w_svarint(out, ins["c"])
        defaults = f["defaults"]
        _w_uvarint(out, len(defaults))
        for d in defaults:
            _w_svarint(out, d)
        _w_svarint(out, f["vararg"])
        params = f["params"]
        _w_uvarint(out, len(params))
        for p in params:
            _w_str(out, p)

    classes = prog["classes"]
    _w_uvarint(out, len(classes))
    for cl in classes:
        _w_str(out, cl["cname"])
        _w_svarint(out, cl["base"])
        methods = cl["methods"]
        _w_uvarint(out, len(methods))
        for m in methods:
            _w_str(out, m["mname"])
            _w_svarint(out, m["mfunc"])

    _w_uvarint(out, prog["entry"])
    return bytes(out)


# --------------------------------------------------------------------------- #
# reference decoder (test / host-side only)                                    #
# --------------------------------------------------------------------------- #
class _Reader:
    def __init__(self, buf):
        self.buf = buf
        self.pos = 0

    def byte(self):
        b = self.buf[self.pos]
        self.pos += 1
        return b

    def uvarint(self):
        shift = 0
        result = 0
        while True:
            b = self.byte()
            result |= (b & 0x7F) << shift
            if not (b & 0x80):
                return result - 1       # undo the +1 NUL-avoidance bias
            shift += 7

    def svarint(self):
        u = self.uvarint()
        return (u >> 1) ^ -(u & 1)

    def string(self):
        n = self.uvarint()
        s = self.buf[self.pos:self.pos + n].decode("utf-8")
        self.pos += n
        return s

    def double(self):
        return float(self.string())


def decode(buf):
    """Reference decode of `.mpyc` bytes back to a `Program` dict."""
    r = _Reader(buf)
    if bytes(r.buf[:4]) != MAGIC:
        raise ValueError("not an .mpyc file (bad magic)")
    r.pos = 4
    version = r.uvarint()
    source = r.string()

    consts = []
    for _ in range(r.uvarint()):
        t = r.string()
        i = 0
        d = 0.0
        s = ""
        if t == "float":
            d = r.double()
        elif t == "str":
            s = r.string()
        elif t == "none":
            pass
        else:
            i = r.svarint()
        consts.append({"t": t, "i": i, "d": d, "s": s})

    names = [r.string() for _ in range(r.uvarint())]
    nglobals = r.uvarint()

    funcs = []
    for _ in range(r.uvarint()):
        name = r.string()
        nparams = r.uvarint()
        nregs = r.uvarint()
        nlocals = r.uvarint()
        code = []
        for _ in range(r.uvarint()):
            packed = r.uvarint()
            op = packed >> 2
            fb = (packed >> 1) & 1
            fc = packed & 1
            ra = r.uvarint()
            b = r.svarint()
            c = r.svarint()
            code.append({"op": op, "ra": ra, "fb": fb, "fc": fc, "b": b, "c": c})
        defaults = [r.svarint() for _ in range(r.uvarint())]
        vararg = r.svarint()
        params = [r.string() for _ in range(r.uvarint())]
        funcs.append({"name": name, "nparams": nparams, "nregs": nregs,
                      "nlocals": nlocals, "code": code, "defaults": defaults,
                      "vararg": vararg, "params": params})

    classes = []
    for _ in range(r.uvarint()):
        cname = r.string()
        base = r.svarint()
        methods = []
        for _ in range(r.uvarint()):
            mname = r.string()
            mfunc = r.svarint()
            methods.append({"mname": mname, "mfunc": mfunc})
        classes.append({"cname": cname, "base": base, "methods": methods})

    entry = r.uvarint()
    return {"version": version, "source": source, "consts": consts,
            "names": names, "nglobals": nglobals, "funcs": funcs,
            "classes": classes, "entry": entry}


def compile_to_mpyc(path):
    """Compile a .py file straight to `.mpyc` bytes (host-side convenience)."""
    import os
    import sys
    here = os.path.dirname(os.path.abspath(__file__))   # tools/minipy
    parent = os.path.dirname(here)                       # tools (has minipy/)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    from minipy import compiler as C
    return encode(C.compile_file(path))


def main(argv=None):
    """`python3 tools/minipy/mpyc.py SRC.py [OUT.mpyc]` -> write binary bytecode.

    With no OUT, writes SRC with its extension replaced by `.mpyc`. The result
    runs directly on the minipy interpreter: `interp OUT.mpyc [args...]`.
    """
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.stderr.write("usage: mpyc.py SRC.py [OUT.mpyc]\n")
        return 1
    src = argv[0]
    out = argv[1] if len(argv) > 1 else (src.rsplit(".", 1)[0] + ".mpyc")
    data = compile_to_mpyc(src)
    with open(out, "wb") as fh:
        fh.write(data)
    sys.stderr.write("wrote %s (%d bytes)\n" % (out, len(data)))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
