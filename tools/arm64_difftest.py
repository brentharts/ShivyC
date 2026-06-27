#!/usr/bin/env python3
"""AArch64 differential tester for the ShivyC arm64 back end.

For each C program it: (1) compiles with ShivyC's own arm64 back end
(`python3 -m shivyc.main --target arm64 -S`), assembles + links the result with
an aarch64 GCC, and runs it under qemu-aarch64; (2) compiles the same C directly
with the aarch64 GCC as an oracle and runs that under qemu; then compares exit
codes. Programs the arm64 back end does not yet support (it says so explicitly,
rather than miscompiling) are reported as SKIP, not FAIL -- the set of SKIPs is
exactly the work remaining.

Toolchain (override via env): CROSS_CC=aarch64-linux-gnu-gcc, QEMU=qemu-aarch64.

Usage:
    python3 tools/arm64_difftest.py            # built-in Stage 2 program set
    python3 tools/arm64_difftest.py a.c b.c    # specific C files
"""
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CROSS_CC = os.environ.get("CROSS_CC", "aarch64-linux-gnu-gcc")
QEMU = os.environ.get("QEMU", "qemu-aarch64")

# Built-in Stage 2 programs: integer-literal returns the back end supports today.
STAGE2 = [
    ("ret0", "int main(){return 0;}"),
    ("ret7", "int main(){return 7;}"),
    ("ret42", "int main(){return 42;}"),
    ("ret200", "int main(){return 200;}"),
    ("ret255", "int main(){return 255;}"),
    ("two_funcs", "long f(){return 100;} int main(){return 42;}"),
]

# Stage 3: locals, add/sub/mul/div/mod, comparisons, if/while.
STAGE3 = [
    ("if_gt", "int main(){int a=7,b=3; if(a>b) return a; return b;}"),
    ("if_ge_false", "int main(){int a=3,b=8; if(a>=b) return 1; return 0;}"),
    ("if_eq", "int main(){int a=5,b=5; if(a==b) return 42; return 0;}"),
    ("if_ne", "int main(){int a=5,b=6; if(a!=b) return 9; return 0;}"),
    ("while_sum", "int main(){int x=0,i=0; while(i<10){x=x+i; i=i+1;} return x;}"),
    ("sum_1_10", "int main(){int s=0,i=1; while(i<=10){s=s+i; i=i+1;} return s;}"),
    ("factorial5", "int main(){int n=5,f=1; while(n>1){f=f*n; n=n-1;} return f;}"),
    ("sub_chain", "int main(){int a=100,b=30,c=8; return a-b-c;}"),
    ("div", "int main(){int a=84,b=2; return a/b;}"),
    ("mod", "int main(){int a=200,b=7; return a%b;}"),
    ("mul_add", "int main(){int a=6,b=7; return a*b+0;}"),
    ("nested_if", "int main(){int x=5; if(x>0){ if(x<10) return 3; } return 9;}"),
    ("countdown", "int main(){int n=10,c=0; while(n>0){c=c+2; n=n-1;} return c;}"),
]

# Stage 4: function calls and recursion (AAPCS64 args in x0-x7).
STAGE4 = [
    ("call_add", "int add(int a,int b){return a+b;} int main(){return add(40,2);}"),
    ("fib10", "int fib(int n){if(n<2)return n; return fib(n-1)+fib(n-2);}"
              " int main(){return fib(10);}"),
    ("fact5", "int fact(int n){if(n<=1)return 1; return n*fact(n-1);}"
              " int main(){return fact(5);}"),
    ("nested_calls", "int sq(int x){return x*x;} int add3(int a,int b,int c)"
                     "{return a+b+c;} int main(){return add3(sq(3),sq(4),0);}"),
    ("five_args", "int g(int a,int b,int c,int d,int e){return a+b+c+d+e;}"
                  " int main(){return g(1,2,3,4,5);}"),
    ("eight_args", "int h(int a,int b,int c,int d,int e,int f,int g,int i)"
                   "{return a+b+c+d+e+f+g+i;} int main(){return h(1,2,3,4,5,6,7,8);}"),
    # >10 live values forces the register allocator to spill the overflow.
    ("spill12", "int main(){int a=1,b=2,c=3,d=4,e=5,f=6,g=7,h=8,i=9,j=10,k=11,"
                "l=12; return a+b+c+d+e+f+g+h+i+j+k+l;}"),
]

# Stage 6: general pointers (AddrOf of a variable, load/store through a pointer).
STAGE6 = [
    ("ptr_store", "int main(){int x=5; int *p=&x; *p=10; return x;}"),
    ("ptr_load", "int main(){int x=5; int *p=&x; return *p;}"),
    ("ptr_copy", "int main(){int x=3,y=4; int *p=&x,*q=&y; *p=*q+1; return x;}"),
    ("ptr_param", "void inc(int *p){*p=*p+1;} int main(){int x=41; inc(&x);"
                  " return x;}"),
    ("double_ptr", "int main(){int x=7; int *p=&x; int **pp=&p; **pp=99;"
                   " return x;}"),
    ("swap", "void swap(int *a,int *b){int t=*a; *a=*b; *b=t;}"
             " int main(){int x=10,y=3; swap(&x,&y); return x;}"),
]

# Stage 7: arrays and indexed access (ReadRel/SetRel; constant + variable index).
STAGE7 = [
    ("arr_const", "int main(){int a[3]; a[0]=7; a[1]=8; return a[0]+a[1];}"),
    ("arr_var", "int main(){int a[5]; int i=2; a[i]=9; return a[i];}"),
    ("arr_init", "int main(){int a[3]={1,2,3}; return a[0]+a[1]+a[2];}"),
    ("char_arr", "int main(){char s[4]; s[0]=72; s[1]=105; s[2]=0;"
                 " return s[0]+s[1];}"),
    ("arr_sumsq", "int main(){int a[5]; int s=0,i=0; while(i<5){a[i]=i*i;"
                  " i=i+1;} i=0; while(i<5){s=s+a[i]; i=i+1;} return s;}"),
    ("ptr_index", "int sum(int *p,int n){int s=0,i=0; while(i<n){s=s+p[i];"
                  " i=i+1;} return s;} int main(){int a[4]; a[0]=10;a[1]=20;"
                  "a[2]=5;a[3]=7; return sum(a,4);}"),
    ("fib_array", "int fib(int n){int f[20]; f[0]=0; f[1]=1; int i=2;"
                  " while(i<=n){f[i]=f[i-1]+f[i-2]; i=i+1;} return f[n];}"
                  " int main(){return fib(10);}"),
]

# Stage 8: structs (member access, pointer-to-struct, whole-struct copy,
# arrays of structs).
STAGE8 = [
    ("struct_basic", "struct P{int x;int y;}; int main(){struct P p; p.x=3;"
                     " p.y=4; return p.x+p.y;}"),
    ("struct_ptr", "struct P{int x;int y;}; int d(struct P *p){return p->x+p->y;}"
                   " int main(){struct P p; p.x=30; p.y=12; return d(&p);}"),
    ("struct_copy", "struct R{int a;int b;int c;}; int main(){struct R r;"
                    " r.a=1;r.b=2;r.c=3; struct R s; s=r; return s.a+s.b+s.c;}"),
    ("struct_pad", "struct P{char a; int b;}; int main(){struct P p; p.a=5;"
                   " p.b=37; return p.a+p.b;}"),
    ("array_of_struct", "struct Pt{int x;int y;}; int main(){struct Pt a[3];"
                        " a[0].x=1; a[1].x=10; a[2].x=100;"
                        " return a[0].x+a[1].x+a[2].x;}"),
    ("struct_loop", "struct Pt{int x;int y;}; int main(){struct Pt a[4]; int i=0;"
                    " while(i<4){a[i].x=i; a[i].y=i*2; i=i+1;} int s=0; i=0;"
                    " while(i<4){s=s+a[i].x+a[i].y; i=i+1;} return s;}"),
]


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def _qemu_exit(binary):
    rc, _, _ = _run([QEMU, binary])
    return rc


def check_toolchain():
    missing = []
    for tool in (CROSS_CC, QEMU):
        rc, _, _ = _run([tool, "--version"])
        if rc != 0:
            missing.append(tool)
    return missing


def test_one(name, src, workdir):
    """Returns (status, detail): status in {PASS, FAIL, SKIP, ERROR}."""
    cpath = os.path.join(workdir, name + ".c")
    with open(cpath, "w") as f:
        f.write(src if src.endswith("\n") else src + "\n")

    # ShivyC arm64 -> .s
    spath = os.path.join(workdir, name + ".s")
    rc, out, err = _run([sys.executable, "-m", "shivyc.main", cpath,
                         "-S", "-o", spath, "--target", "arm64"])
    blob = (out + err).lower()
    if "not implemented" in blob or "stage 2" in blob:
        return "SKIP", "arm64 back end does not support this yet"
    if rc != 0 or not os.path.exists(spath):
        return "ERROR", "shivyc arm64 failed: %s" % (err.strip()[:200])

    # assemble + link our asm
    mybin = os.path.join(workdir, name + ".my")
    rc, _, err = _run([CROSS_CC, "-static", spath, "-o", mybin])
    if rc != 0:
        return "ERROR", "assembling our asm failed: %s" % err.strip()[:200]

    # oracle: gcc-arm64 straight from C
    orabin = os.path.join(workdir, name + ".ora")
    rc, _, err = _run([CROSS_CC, "-static", cpath, "-o", orabin])
    if rc != 0:
        return "ERROR", "oracle compile failed: %s" % err.strip()[:200]

    mine = _qemu_exit(mybin)
    ora = _qemu_exit(orabin)
    if mine == ora:
        return "PASS", "exit=%d" % mine
    return "FAIL", "mine=%d oracle=%d" % (mine, ora)


def main(argv):
    missing = check_toolchain()
    if missing:
        print("missing toolchain: %s" % ", ".join(missing))
        print("install e.g.: apt install gcc-aarch64-linux-gnu qemu-user")
        return 2

    if len(argv) > 1:
        progs = []
        for path in argv[1:]:
            with open(path) as f:
                progs.append((os.path.basename(path), f.read()))
    else:
        progs = STAGE2 + STAGE3 + STAGE4 + STAGE6 + STAGE7 + STAGE8

    workdir = tempfile.mkdtemp(prefix="arm64diff-")
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0, "ERROR": 0}
    for name, src in progs:
        status, detail = test_one(name, src, workdir)
        counts[status] += 1
        print("  %-5s %-12s %s" % (status, name, detail))

    print("\narm64 difftest: %d pass, %d fail, %d skip, %d error"
          % (counts["PASS"], counts["FAIL"], counts["SKIP"], counts["ERROR"]))
    return 1 if (counts["FAIL"] or counts["ERROR"]) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
