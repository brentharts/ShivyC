#!/bin/sh
# End-to-end demo: compile a program against the PACKAGED musl (no glibc).
# Usage: PYTHONPATH=<ShivyC> sh build_musl_demo.sh
set -e
DEST=${1:-/tmp/musldemo}
python3 - "$DEST" << 'PY'
import sys
from shivyc import musl
t = musl.materialize(sys.argv[1])           # write musl headers to DEST
for n in ("strlen.c","strcmp.c","memcpy.c","strcpy.c","stpcpy.c"):
    t.write_source("string", n)             # extract only the parts we need
print("materialized to", t.root)
print("PUBLIC :", " ".join(t.public_cflags()))
print("INTERNAL:", " ".join(t.internal_cflags()))
PY
echo "Now compile user code with the PUBLIC cflags and musl sources with the"
echo "INTERNAL cflags, then: ld -static -nostdlib <objs> <startup> -o app"
