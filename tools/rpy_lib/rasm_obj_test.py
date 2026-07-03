"""End-to-end test for the rasm assembler + ELF writer.

For each C program: compile with ShivyCX to assembly, then assemble it two ways
-- with rasm and with GNU `as` -- link both with gcc, run both, and require the
exit codes (and any stdout) to match. This validates that rasm's objects are
correct and linkable, without requiring byte-identical output (rasm does not yet
do branch relaxation, so its encodings can be larger but equivalent).
"""
import os
import sys
import subprocess
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rasm_obj

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHIVYC_MAIN = os.path.join(REPO, "shivyc", "main.py")

PROGRAMS = {
    "arith": """
int main() { int a = 7, b = 3; return a*b - b + (a/b) + (a%b); }
""",
    "recursion": """
int fib(int n){ if (n < 2) return n; return fib(n-1) + fib(n-2); }
int main(){ return fib(10); }
""",
    "loop_globals": """
int arr[8];
long acc = 0;
int main(){
  int i;
  for (i = 0; i < 8; i++) { arr[i] = i*i; acc += arr[i]; }
  return (int)acc;
}
""",
    "bitops": """
int main(){
  unsigned x = 0xF0; unsigned y = 0x18;
  return (int)((x | y) & 0xFF) ^ (x >> 4) ^ (y << 1) & 0x7F;
}
""",
    "conditions": """
int classify(int n){
  if (n < 0) return 1;
  if (n == 0) return 2;
  if (n > 100) return 3;
  return 4;
}
int main(){ return classify(-5) + classify(0)*10 + classify(200)*100 + classify(50)*1000; }
""",
    "fnptr_data": """
int inc(int x){ return x+1; }
int dec(int x){ return x-1; }
int (*ops[2])(int) = { inc, dec };
int main(){ return ops[0](10) + ops[1](10); }
""",
    "nested_calls": """
int f(int x){ return x*2; }
int g(int x){ return f(x)+f(x+1); }
int h(int x){ return g(f(x)) - g(x); }
int main(){ int s=0,i; for(i=0;i<5;i++) s += h(i); return s & 0xFF; }
""",
    "array_reduce": """
int data[6] = {5, 9, 2, 7, 1, 8};
int maxv(int* a, int n){ int m = a[0], i; for(i=1;i<n;i++){ if(a[i]>m) m=a[i]; } return m; }
int main(){ int s=0,i; for(i=0;i<6;i++) s += data[i]; return s + maxv(data,6); }
""",
    "floats": """
double addd(double a, double b){ return a + b; }
int cmp(double a, double b){ return a < b ? 1 : 0; }
int main(){ double d = 3.5; int k = (int)(addd(d, 2.0) * 2.0); return k + cmp(1.0, 2.0); }
""",
    "ptr_struct": """
struct P { int x; long y; };
long sum(struct P* p){ return p->x + p->y; }
int main(){ struct P p; p.x = 11; p.y = 31; return (int)sum(&p); }
""",
}


def sh(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)


def build_and_run(sfile, use_rasm, workdir):
    obj = os.path.join(workdir, "o_%s.o" % ("rasm" if use_rasm else "as"))
    if use_rasm:
        elf = rasm_obj.assemble_to_elf(open(sfile).read())
        open(obj, "wb").write(bytes(elf))
    else:
        r = sh(["as", "--64", "-o", obj, sfile])
        if r.returncode != 0:
            return None, r.stderr.decode()
    binf = obj + ".bin"
    r = sh(["gcc", "-no-pie", obj, "-o", binf])
    if r.returncode != 0:
        return None, r.stderr.decode()
    r = sh([binf])
    return (r.returncode, r.stdout.decode()), ""


def main():
    tmp = tempfile.mkdtemp()
    npass = 0
    fails = []
    for name in sorted(PROGRAMS.keys()):
        cfile = os.path.join(tmp, name + ".c")
        sfile = os.path.join(tmp, name + ".s")
        open(cfile, "w").write(PROGRAMS[name])
        env = dict(os.environ)
        env["PYTHONPATH"] = REPO
        r = subprocess.run([sys.executable, SHIVYC_MAIN, "-S", cfile,
                            "-o", sfile], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if not os.path.exists(sfile):
            fails.append((name, "ShivyCX failed: " + r.stderr.decode()[:200]))
            continue
        rasm_res, rerr = build_and_run(sfile, True, tmp)
        as_res, aerr = build_and_run(sfile, False, tmp)
        if rasm_res is None:
            fails.append((name, "rasm build failed: " + rerr[:200]))
            continue
        if as_res is None:
            fails.append((name, "as build failed: " + aerr[:200]))
            continue
        if rasm_res == as_res:
            npass += 1
            print("  ok    %-14s exit=%d" % (name, rasm_res[0]))
        else:
            fails.append((name, "rasm=%r as=%r" % (rasm_res, as_res)))

    print("\nrasm end-to-end: %d/%d passed" % (npass, len(PROGRAMS)))
    if fails:
        print("FAILURES:")
        for n, msg in fails:
            print("  %-14s %s" % (n, msg))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
