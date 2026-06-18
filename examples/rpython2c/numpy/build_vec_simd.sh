#!/bin/bash
# Transpile the f32 element-wise kernels and compile them with ShivyCX, showing
# that ShivyCX's own contract vectorizer emits packed-single SSE (mulps/addps).
set -e
HERE=$(dirname "$0"); ROOT=$(git -C "$HERE" rev-parse --show-toplevel)
OUT=$(mktemp -d)
python3 "$ROOT/tools/py2c.py" "$HERE/vec_simd.py" --out "$OUT" >/dev/null
# kernels are pure float C (no runtime); drop the include and add a call site
# whose sizes ShivyCX can prove (literal malloc bytes + literal length).
sed '/#include "shivyc_rt.h"/d' "$OUT/vec_simd.c" > "$OUT/k.c"
cat >> "$OUT/k.c" <<'C'
void *malloc(unsigned long);
int main(void){
    float *a=malloc(1024*4),*b=malloc(1024*4),*o=malloc(1024*4);
    int i=0; while(i<1024){ a[i]=i; b[i]=2*i; i=i+1; }
    vadd(a,b,o,1024);    int s1=((int)o[10])%250;   /* 10+20 = 30 */
    vmul(a,b,o,1024);    int s2=((int)o[10])%250;   /* 10*20 = 200 */
    saxpy(3.0f,a,b,o,1024); int s3=((int)o[10])%250; /* 3*10+20 = 50 */
    return (s1+s2+s3)%250;   /* 280 -> 30 */
}
C
echo "== compiling kernels with ShivyCX =="
python3 -m shivyc.main --no-cache "$OUT/k.c" -o "$OUT/k" | grep simd-contracts
echo "== packed-single SSE that ShivyCX emitted =="
grep -oE "mulps|addps|subps|movups|shufps" "$OUT/k.s" | sort | uniq -c
set +e; "$OUT/k"; echo "exit code = $? (expect 30: vadd=30, vmul=200, saxpy=50 -> 280 % 250)"
