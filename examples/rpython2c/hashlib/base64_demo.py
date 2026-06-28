"""Base64 encode/decode over the rpython `rpy_base64` library.

Pure byte regrouping, so the transpiled program prints exactly what CPython's
`base64` module produces -- a byte-for-byte differential check.
"""
import rpy_base64


def show(label: "str", s: "str") -> None:
    enc = rpy_base64.b64encode_str(s)
    # decode back and rebuild the string to confirm the round trip
    dec = rpy_base64.b64decode(enc)
    rt = ""
    i = 0
    while i < len(dec):
        rt = rt + chr(dec[i])
        i += 1
    print(label + ": " + enc + " -> " + rt)


def main() -> "int":
    show("empty", "")
    show("f", "f")
    show("fo", "fo")
    show("foo", "foo")
    show("foobar", "foobar")
    show("phrase", "ShivyCX rpython to C")
    return 0
