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
        progs = STAGE2

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
