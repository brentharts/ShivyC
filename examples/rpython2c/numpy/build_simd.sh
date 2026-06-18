#!/bin/bash
# Transpile simd_sum.py with py2c, then compile with ShivyCX and show that the
# generated contracts make it emit a vectorized (SSE2) reduction.
set -e
ROOT=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
OUT=$(mktemp -d)
python3 "$ROOT/tools/py2c.py" "$(dirname "$0")/simd_sum.py" --out "$OUT" >/dev/null
echo "== py2c emitted these contract clauses =="
sed -n '/^int array_sum/,/^{/p' "$OUT/simd_sum.c"

# array_sum is pure scalar C (no runtime); drop the runtime include and add a
# call site whose length ShivyCX can prove (literal malloc size + literal len).
sed '/#include "shivyc_rt.h"/d' "$OUT/simd_sum.c" > "$OUT/k.c"
cat >> "$OUT/k.c" <<'C'
void *malloc(unsigned long);
int main(void){
    int *buf = malloc(4096 * 4);
    int i = 0; while (i < 4096) { buf[i] = i; i = i + 1; }
    return array_sum(buf, 4096) % 250;   /* 8386560 % 250 == 60 */
}
C
echo "== compiling with ShivyCX =="
python3 -m shivyc.main --no-cache "$OUT/k.c" -o "$OUT/k"
echo "== SSE2 instructions in array_sum =="
grep -oE "paddd|movdqa|movdqu|psrldq|pxor|punpck" "$OUT/k.s" | sort | uniq -c
set +e
"$OUT/k"; rc=$?
echo "exit code = $rc (expect 60 -> reduction result correct)"
