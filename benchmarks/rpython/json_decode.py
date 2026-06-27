"""Typed JSON decoding -- parse a record into a struct, N times.

Each iteration deserializes one JSON object into a `User` via

    json.loads(s, object_hook=rpy.json.generate_decoder(User))

and folds its fields into a checksum (mod 256) so the parse cannot be optimized
away and all four backends must agree.

This is the suite's typed-deserialization benchmark. The contrast is in *how* the
four backends get from bytes to a typed object:

  * CPython / PyPy3 -- `import rpy` provides a real object_hook; json.loads
    tokenizes into a dict, boxes every value, then the hook builds a User.
  * py2c + gcc / self-hosted -- the translator recognizes the
    rpy.json.generate_decoder(User) call, reads User's field layout, and emits a
    specialized C parser `_json_decode_User` that scans the bytes once and writes
    straight into the POD struct: no dict, no boxing, no Python callback. `import
    json` / `import rpy` are not translated.

Record count N is read from argv. The same record string is parsed each
iteration (a fair parse-throughput comparison: every backend re-parses from
scratch -- json.loads does not cache).
"""

import sys
import json
import rpy


class User:
    def __init__(self, name: "char*", age: "int", score: "float",
                 active: "bool", uid: "long"):
        self.name = name
        self.age = age
        self.score = score
        self.active = active
        self.uid = uid


def main() -> int:
    n = int(sys.argv[1])
    s = ('{"name": "Ada Lovelace", "age": 36, "score": 99.5, '
         '"active": true, "uid": 1815}')
    decode = rpy.json.generate_decoder(User)   # build the decoder once
    acc: "long" = 0
    i = 0
    while i < n:
        u = json.loads(s, object_hook=decode)
        acc = acc + u.age + len(u.name) + int(u.score) + u.uid
        if u.active:
            acc = acc + 1
        i = i + 1
    return int(acc) % 256


if __name__ == "__main__":
    sys.exit(main())
