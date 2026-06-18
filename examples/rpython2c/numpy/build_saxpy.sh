#!/bin/bash
# Transpile the float32 kernels and show that py2c's native output vectorizes to
# SSE mulps/addps under gcc -O3, then run for correctness.
set -e
HERE=$(dirname "$0"); ROOT=$(git -C "$HERE" rev-parse --show-toplevel)
OUT=$(mktemp -d)
python3 "$ROOT/tools/py2c.py" "$HERE/saxpy.py" --out "$OUT" >/dev/null
echo "== native element-wise C py2c emitted =="
grep -E "out\[i\] = " "$OUT/saxpy.c"
echo "== contracts (vadd256 inferred from f32[256], no user assert) =="
sed -n '/void vadd256/,/^{/p' "$OUT/saxpy.c"
# gcc compiles plain C; strip the runtime include and the ShivyCX assert clauses
sed -e '/#include "shivyc_rt.h"/d' -e '/^assert /d' "$OUT/saxpy.c" > "$OUT/k.c"
gcc -O3 -msse2 -S "$OUT/k.c" -o "$OUT/k.s"
echo "== SSE single-precision instructions emitted =="
grep -oE "mulps|addps|movups|movaps" "$OUT/k.s" | sort | uniq -c
echo "== run for correctness =="
gcc -O3 -msse2 "$OUT/k.c" "$HERE/saxpy_main.c" -o "$OUT/run"
"$OUT/run"
