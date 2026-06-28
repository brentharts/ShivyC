"""rpy_hashlib -- a restricted-Python (rpython) SHA-256, ported from
micropython-lib's `python-stdlib/hashlib-sha256` (itself ported from CPython by
Paul Sokolovsky).

SHA-256 is pure 32-bit modular arithmetic -- right rotations, shifts, and adds
taken mod 2**32. In rpython that maps to the `unsigned` (C `uint32_t`) type,
whose add/shift wrap mod 2**32, so the whole compression lowers to tight,
allocation-free C. Every mod-2**32 reduction goes through the typed helper `_u32`
rather than a bare `& 0xFFFFFFFF` literal: that keeps the C type `unsigned` (a
literal mask would widen the value back to the boxed object type) while still
performing the reduction under CPython, where the annotations are ignored and
ints are unbounded. So the module runs unchanged under CPython and a transpiled
digest is byte-for-byte identical to the genuine `hashlib.sha256`.

The structure is retargeted at the rpython dialect rather than copied verbatim:
upstream uses `lambda` helpers, 64 unrolled `RND(...)` calls with tuple-unpack
assignment, and a `sha` base class holding `list` state -- all of which force the
boxed/slow path or miss-compile today. This port uses free typed functions, flat
`list[unsigned]` arrays, eight scalar working words rotated by hand, and the
canonical 64-iteration compression loop. The output is identical.

API (input bytes are a plain `list[int]`, each element 0..255):
    sha256_hex(data) -> str   the 64-char lowercase hex digest
    sha256_str(s)    -> str   hex digest of an ASCII string's bytes
"""


def _u32(x: "unsigned") -> "unsigned":
    """Reduce mod 2**32. A no-op on C `unsigned`; the actual reduction under
    CPython, whose ints are unbounded."""
    return x & 0xFFFFFFFF


def _rotr(x: "unsigned", n: "int") -> "unsigned":
    """32-bit right rotation of `x` by `n` bits."""
    return _u32((x >> n) | (x << (32 - n)))


def _ch(x: "unsigned", y: "unsigned", z: "unsigned") -> "unsigned":
    return (x & y) ^ ((x ^ 0xFFFFFFFF) & z)


def _maj(x: "unsigned", y: "unsigned", z: "unsigned") -> "unsigned":
    return (x & y) ^ (x & z) ^ (y & z)


def _word_hex(v: "unsigned") -> "str":
    """The 8-character big-endian lowercase hex of a 32-bit word."""
    hexd = "0123456789abcdef"
    out = ""
    shift = 28
    while shift >= 0:
        out = out + hexd[(v >> shift) & 15]
        shift -= 4
    return out


def sha256_hex(data: "list[int]") -> "str":
    """SHA-256 of the byte list `data` (each element 0..255) as a 64-character
    lowercase hex digest, identical to hashlib.sha256(bytes(data)).hexdigest()."""
    k: "list[unsigned]" = [
        0x428A2F98, 0x71374491, 0xB5C0FBCF, 0xE9B5DBA5,
        0x3956C25B, 0x59F111F1, 0x923F82A4, 0xAB1C5ED5,
        0xD807AA98, 0x12835B01, 0x243185BE, 0x550C7DC3,
        0x72BE5D74, 0x80DEB1FE, 0x9BDC06A7, 0xC19BF174,
        0xE49B69C1, 0xEFBE4786, 0x0FC19DC6, 0x240CA1CC,
        0x2DE92C6F, 0x4A7484AA, 0x5CB0A9DC, 0x76F988DA,
        0x983E5152, 0xA831C66D, 0xB00327C8, 0xBF597FC7,
        0xC6E00BF3, 0xD5A79147, 0x06CA6351, 0x14292967,
        0x27B70A85, 0x2E1B2138, 0x4D2C6DFC, 0x53380D13,
        0x650A7354, 0x766A0ABB, 0x81C2C92E, 0x92722C85,
        0xA2BFE8A1, 0xA81A664B, 0xC24B8B70, 0xC76C51A3,
        0xD192E819, 0xD6990624, 0xF40E3585, 0x106AA070,
        0x19A4C116, 0x1E376C08, 0x2748774C, 0x34B0BCB5,
        0x391C0CB3, 0x4ED8AA4A, 0x5B9CCA4F, 0x682E6FF3,
        0x748F82EE, 0x78A5636F, 0x84C87814, 0x8CC70208,
        0x90BEFFFA, 0xA4506CEB, 0xBEF9A3F7, 0xC67178F2,
    ]

    n = len(data)
    # Pad: a 0x80 byte, zeros to 56 mod 64, then the 64-bit big-endian bit length.
    msg: "list[unsigned]" = []
    i = 0
    while i < n:
        msg.append(_u32(data[i] & 0xFF))
        i += 1
    bitlen: "long" = n * 8
    msg.append(0x80)
    while (len(msg) % 64) != 56:
        msg.append(0)
    i = 7
    while i >= 0:
        msg.append(_u32((bitlen >> (8 * i)) & 0xFF))
        i -= 1

    h0: "unsigned" = 0x6A09E667
    h1: "unsigned" = 0xBB67AE85
    h2: "unsigned" = 0x3C6EF372
    h3: "unsigned" = 0xA54FF53A
    h4: "unsigned" = 0x510E527F
    h5: "unsigned" = 0x9B05688C
    h6: "unsigned" = 0x1F83D9AB
    h7: "unsigned" = 0x5BE0CD19

    nblocks = len(msg) // 64
    blk = 0
    while blk < nblocks:
        base = blk * 64

        w: "list[unsigned]" = []
        i = 0
        while i < 16:
            j = base + 4 * i
            w.append(_u32((msg[j] << 24) | (msg[j + 1] << 16)
                          | (msg[j + 2] << 8) | msg[j + 3]))
            i += 1
        while i < 64:
            x15 = w[i - 15]
            s0 = _rotr(x15, 7) ^ _rotr(x15, 18) ^ _u32(x15 >> 3)
            x2 = w[i - 2]
            s1 = _rotr(x2, 17) ^ _rotr(x2, 19) ^ _u32(x2 >> 10)
            w.append(_u32(w[i - 16] + s0 + w[i - 7] + s1))
            i += 1

        a: "unsigned" = h0
        b: "unsigned" = h1
        c: "unsigned" = h2
        d: "unsigned" = h3
        e: "unsigned" = h4
        f: "unsigned" = h5
        g: "unsigned" = h6
        h: "unsigned" = h7

        i = 0
        while i < 64:
            big_s1 = _rotr(e, 6) ^ _rotr(e, 11) ^ _rotr(e, 25)
            t1 = _u32(h + big_s1 + _ch(e, f, g) + k[i] + w[i])
            big_s0 = _rotr(a, 2) ^ _rotr(a, 13) ^ _rotr(a, 22)
            t2 = _u32(big_s0 + _maj(a, b, c))
            h = g
            g = f
            f = e
            e = _u32(d + t1)
            d = c
            c = b
            b = a
            a = _u32(t1 + t2)
            i += 1

        h0 = _u32(h0 + a)
        h1 = _u32(h1 + b)
        h2 = _u32(h2 + c)
        h3 = _u32(h3 + d)
        h4 = _u32(h4 + e)
        h5 = _u32(h5 + f)
        h6 = _u32(h6 + g)
        h7 = _u32(h7 + h)
        blk += 1

    return (_word_hex(h0) + _word_hex(h1) + _word_hex(h2) + _word_hex(h3)
            + _word_hex(h4) + _word_hex(h5) + _word_hex(h6) + _word_hex(h7))


def sha256_str(s: "str") -> "str":
    """SHA-256 hex digest of an ASCII string's bytes (s.encode() for ASCII)."""
    data: "list[int]" = []
    i = 0
    while i < len(s):
        data.append(ord(s[i]) & 0xFF)
        i += 1
    return sha256_hex(data)
