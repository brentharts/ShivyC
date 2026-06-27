#!/usr/bin/env python3
"""RISC-V 64 differential tester for the ShivyC riscv64 back end.

Mirrors tools/arm64_difftest.py: for each C program, compile it with ShivyC
(`--target riscv64`) to assembly, assemble + link with the RISC-V cross gcc,
run under qemu-riscv64, and compare the process exit code against the same
program compiled straight from C by gcc (the oracle). The two exit codes must
match (mod 256).

The riscv64 back end currently implements the integer core (locals, +-*/% , the
six comparisons, if/while, direct calls, recursion). Programs that use features
it does not yet lower (floats, pointers, arrays, structs, globals) make ShivyC
raise; those are reported SKIP, not FAIL -- the back end refuses rather than
miscompile.

Toolchain (override via env): CROSS_CC=riscv64-linux-gnu-gcc, QEMU=qemu-riscv64.
"""
import os
import subprocess
import sys
import tempfile

CROSS_CC = os.environ.get("CROSS_CC", "riscv64-linux-gnu-gcc")
QEMU = os.environ.get("QEMU", "qemu-riscv64")

# Integer-core corpus exercising the shared linear-scan allocator on a second
# ISA: constants, arithmetic, division/modulo, comparisons, if/while control
# flow, leaf and recursive calls, register pressure (spills), the copy-
# coalescing safety check (swaps), and cross-call liveness.
CORE = [
    ("rv_const", "int main(){return 42;}"),
    ("rv_arith", "int main(){int a=2,b=3,c=4; return a*b+c-1;}"),
    ("rv_div_mod", "int main(){int a=100,b=7; return a/b + a%b;}"),
    ("rv_neg", "int main(){int a=3,b=10; return a-b;}"),
    ("rv_cmp_all", "int main(){int a=3,b=5; int r=0;"
                   " if(a<b)r=r+1; if(b>a)r=r+10; if(a<=3)r=r+100;"
                   " if(b>=5)r=r+1000; if(a==3)r=r+10000; if(a!=b)r=r+100000;"
                   " return r%256;}"),
    ("rv_if_else", "int cls(int x){if(x<0)return 0; if(x<10)return 1;"
                   " if(x<100)return 2; return 3;}"
                   " int main(){return cls(5)+cls(50)*4+cls(500)*16;}"),
    ("rv_while", "int main(){int s=0,i=0; while(i<20){s=s+i; i=i+1;}"
                 " return s%256;}"),
    ("rv_nested_loop", "int main(){int g=0,i=0; while(i<10){int j=0;"
                       " while(j<10){g=g+1; j=j+1;} i=i+1;} return g%256;}"),
    ("rv_leaf_call", "int sq(int x){return x*x;} int main(){return sq(12);}"),
    ("rv_fib", "int fib(int n){if(n<2)return n; return fib(n-1)+fib(n-2);}"
               " int main(){return fib(11)%256;}"),
    ("rv_mutual", "int isodd(int n); int iseven(int n){if(n==0)return 1;"
                  " return isodd(n-1);} int isodd(int n){if(n==0)return 0;"
                  " return iseven(n-1);} int main(){return iseven(10);}"),
    ("rv_multi_arg", "int f(int a,int b,int c,int d){return a*1000+b*100+"
                     "c*10+d;} int main(){return f(1,2,3,4)%256;}"),
    ("rv_args_after_call", "int h(int a,int b,int c){return a+b+c;}"
                           " int main(){int p=2,q=3,r=4; int s=h(p,q,r);"
                           " return s+p+q+r;}"),
    ("rv_swap", "int main(){int a=3,b=7; int t=a; a=b; b=t;"
                " return a*10+b;}"),
    ("rv_fib_iter", "int main(){int a=0,b=1,i=0; while(i<10){int t=a+b;"
                    " a=b; b=t; i=i+1;} return b;}"),
    ("rv_pressure", "int main(){int a=1,b=2,c=3,d=4,e=5,f=6,g=7,h=8,i=9,"
                    "j=10,k=11,l=12,m=13,n=14,o=15,p=16; return (a+b+c+d+e+"
                    "f+g+h+i+j+k+l+m+n+o+p+a*b+c*d)%256;}"),
    ("rv_spills_cross_call", "int sq(int x){return x*x;} int main(){"
                             "int a=1,b=2,c=3,d=4,e=5,f=6,g=7,h=8,i=9,j=10,"
                             "k=11,l=12; int s=sq(a); return (s+a+b+c+d+e+f+"
                             "g+h+i+j+k+l)%256;}"),
    ("rv_tail_rec", "int rec(int n,int acc){if(n==0)return acc;"
                    " return rec(n-1,acc+n);} int main(){return rec(10,0);}"),
]


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


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

    spath = os.path.join(workdir, name + ".s")
    rc, out, err = _run([sys.executable, "-m", "shivyc.main", cpath,
                         "-S", "-o", spath, "--target", "riscv64"])
    blob = (out + err).lower()
    if "not implemented" in blob or "integer core" in blob:
        return "SKIP", "riscv64 back end does not support this yet"
    if rc != 0 or not os.path.exists(spath):
        return "ERROR", "shivyc riscv64 failed: %s" % (err.strip()[:200])

    mybin = os.path.join(workdir, name + ".my")
    rc, _, err = _run([CROSS_CC, "-static", spath, "-o", mybin])
    if rc != 0:
        return "ERROR", "assembling our asm failed: %s" % err.strip()[:200]

    orabin = os.path.join(workdir, name + ".ora")
    rc, _, err = _run([CROSS_CC, "-static", cpath, "-o", orabin])
    if rc != 0:
        return "ERROR", "oracle compile failed: %s" % err.strip()[:200]

    mine, _, _ = _run([QEMU, mybin])
    ora, _, _ = _run([QEMU, orabin])
    if mine == ora:
        return "PASS", "exit=%d" % mine
    return "FAIL", "mine=%d oracle=%d" % (mine, ora)


def main(argv):
    missing = check_toolchain()
    if missing:
        print("missing toolchain: %s" % ", ".join(missing))
        print("install e.g.: apt install gcc-riscv64-linux-gnu qemu-user")
        return 2

    if len(argv) > 1:
        progs = []
        for path in argv[1:]:
            with open(path) as f:
                progs.append((os.path.basename(path), f.read()))
    else:
        progs = CORE

    workdir = tempfile.mkdtemp(prefix="riscv64diff-")
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0, "ERROR": 0}
    for name, src in progs:
        status, detail = test_one(name, src, workdir)
        counts[status] += 1
        print("  %-5s %-20s %s" % (status, name, detail))

    print("\nriscv64 difftest: %d pass, %d fail, %d skip, %d error"
          % (counts["PASS"], counts["FAIL"], counts["SKIP"], counts["ERROR"]))
    return 1 if (counts["FAIL"] or counts["ERROR"]) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
