"""rpy_base64 -- a restricted-Python (rpython) Base64 codec, ported from
micropython-lib's `python-stdlib/binascii` base64 routines (a2b_base64 /
b2a_base64), which implement RFC 4648.

Base64 is pure byte/bit regrouping -- three input bytes map to four 6-bit
symbols and back -- so it lowers to flat C over `list[int]` byte buffers with no
boxing, and runs unchanged under CPython, letting a transpiled result be diffed
against the genuine `base64` module.

Retargeted to the rpython dialect: the encoder uses explicit three-byte grouping
(rather than upstream's unmasked big-int shift accumulator, which would overflow
a fixed-width C integer), and the decoder keeps the bit buffer masked to its live
low bits. The output is byte-for-byte identical to CPython's base64.

API (bytes are a plain `list[int]`, each element 0..255):
    b64encode(data)    -> str         standard RFC 4648 base64 (no newline)
    b64encode_str(s)   -> str         base64 of an ASCII string's bytes
    b64decode(s)       -> list[int]   decode base64 text back to bytes
"""

def b64encode(data: "list[int]") -> "str":
    """Standard RFC 4648 Base64 of the byte list `data`, '='-padded, no trailing
    newline -- identical to base64.b64encode(bytes(data)).decode()."""
    tbl = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    out = ""
    n = len(data)
    i = 0
    while i + 3 <= n:
        c0 = data[i]
        c1 = data[i + 1]
        c2 = data[i + 2]
        out = out + tbl[c0 >> 2]
        out = out + tbl[((c0 & 3) << 4) | (c1 >> 4)]
        out = out + tbl[((c1 & 15) << 2) | (c2 >> 6)]
        out = out + tbl[c2 & 63]
        i += 3
    rem = n - i
    if rem == 1:
        c0 = data[i]
        out = out + tbl[c0 >> 2]
        out = out + tbl[(c0 & 3) << 4]
        out = out + "=="
    elif rem == 2:
        c0 = data[i]
        c1 = data[i + 1]
        out = out + tbl[c0 >> 2]
        out = out + tbl[((c0 & 3) << 4) | (c1 >> 4)]
        out = out + tbl[(c1 & 15) << 2]
        out = out + "="
    return out


def b64encode_str(s: "str") -> "str":
    """Base64 of an ASCII string's bytes."""
    data: "list[int]" = []
    i = 0
    while i < len(s):
        data.append(ord(s[i]) & 0xFF)
        i += 1
    return b64encode(data)


def _b64_value(ch: "int") -> "int":
    """6-bit value of a Base64 symbol code, or -1 if not a Base64 symbol."""
    # 'A'-'Z' -> 0..25, 'a'-'z' -> 26..51, '0'-'9' -> 52..61, '+' -> 62, '/' -> 63
    if ch >= 65 and ch <= 90:
        return ch - 65
    if ch >= 97 and ch <= 122:
        return ch - 97 + 26
    if ch >= 48 and ch <= 57:
        return ch - 48 + 52
    if ch == 43:
        return 62
    if ch == 47:
        return 63
    return -1


def b64decode(s: "str") -> "list[int]":
    """Decode RFC 4648 Base64 text to its bytes -- identical to
    list(base64.b64decode(s)). Non-alphabet characters are skipped; padding is
    handled like binascii.a2b_base64."""
    res: "list[int]" = []
    leftchar = 0
    leftbits = 0
    quad_pos = 0
    last_was_pad = False
    i = 0
    n = len(s)
    while i < n:
        ch = ord(s[i])
        i += 1
        if ch == 61:                       # '='
            if quad_pos > 2 or (quad_pos == 2 and last_was_pad):
                break
            last_was_pad = True
        else:
            v = _b64_value(ch)
            if v == -1:
                continue                   # skip stray characters (newlines, etc.)
            quad_pos = (quad_pos + 1) & 3
            leftchar = ((leftchar << 6) | v) & 0xFFFF
            leftbits += 6
            if leftbits >= 8:
                leftbits -= 8
                res.append((leftchar >> leftbits) & 0xFF)
                leftchar = leftchar & ((1 << leftbits) - 1)
            last_was_pad = False
    return res
