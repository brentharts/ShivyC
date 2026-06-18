#!/bin/bash
# Transpile an rpython example to C, compile with gcc, and run it.
# Usage: run.sh <example.py> [main.c]   (run from this dir)
set -e
PY=${1:-vectorize.py}; MAIN=${2:-main.c}
ROOT=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
OUT=$(mktemp -d)
python3 "$ROOT/tools/py2c.py" "$PY" --out "$OUT"
cp "$MAIN" "$OUT"/
cd "$OUT"
gcc -O2 -I. -c shivyc_rt.c -o shivyc_rt.o
gcc -O2 -I. -c "$(basename "${PY%.py}").c" -o mod.o 2>/dev/null || gcc -O2 -I. -c *.c 2>/dev/null
gcc -O2 -I. -c "$(basename "$MAIN")" -o main.o
gcc -no-pie main.o mod.o shivyc_rt.o -o prog
./prog
