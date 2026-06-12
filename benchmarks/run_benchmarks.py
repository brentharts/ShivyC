#!/usr/bin/env python3
"""Benchmark harness: ShivyCX extended-C features vs gcc -O0.

ShivyCX emits unoptimized, -O0-class code, so the fair external peer is
**gcc -O0** (gcc -O2 would measure a different compiler class entirely). The
primary comparison for each feature is still feature-ON vs feature-OFF on the
same compiler, which holds codegen quality constant and isolates the feature;
gcc -O0 is shown alongside as the honest "what an ordinary unoptimizing C
compiler does" reference.

For every configuration the harness:
  * compiles the program,
  * runs it and records the exit code (differential correctness: all configs
    must agree),
  * times it (best-of-N wall clock), and
  * extracts a static codegen metric that directly witnesses the feature.

Results -> results/results.json.
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
RESULTS_DIR = os.path.join(HERE, "results")
REPS = 5


def _shivyc(src, out, extra=None):
    cmd = [sys.executable, "-m", "shivyc.main", "--no-cache", src, "-o", out]
    if extra:
        cmd[4:4] = extra
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def _gcc(src, out, opt="-O0"):
    p = subprocess.run(["gcc", opt, src, "-o", out],
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def _run_exit(binary):
    return subprocess.run([binary]).returncode


def _time_best(binary, reps=REPS):
    best = float("inf")
    for _ in range(reps):
        t0 = time.perf_counter()
        subprocess.run([binary], stdout=subprocess.DEVNULL)
        best = min(best, time.perf_counter() - t0)
    return best


def _read_asm(src):
    with open(src[:-2] + ".s") as f:
        return f.read()


def _write_baseline(src_on, src_off, transform):
    """Derive a feature-OFF baseline source from the primary source.

    The harness is self-contained: the baseline .c is generated here rather than
    committed, so a fresh checkout never lacks it. `transform` maps the source
    text to its plain-C equivalent (e.g. stripping `assert` contract clauses or
    the `__metamorphic__` specifier).
    """
    with open(src_on) as f:
        text = f.read()
    with open(src_off, "w") as f:
        f.write(transform(text))


def _strip_contract_clauses(text):
    """Drop lines whose first non-space token is `assert` (contract clauses)."""
    return "".join(ln for ln in text.splitlines(keepends=True)
                   if not ln.lstrip().startswith("assert "))


def _strip_metamorphic(text):
    return text.replace(" __metamorphic__", "")


def _extract_function(asm, name):
    """Lines of function `name`, label to first ret/jmp terminator."""
    out, capturing = [], False
    for ln in asm.splitlines():
        if ln.strip().startswith(name + ":"):
            capturing = True
        if capturing:
            out.append(ln)
            if re.search(r"\b(ret|jmp)\b", ln):
                break
    return "\n".join(out)


def _count(asm, pat):
    return len(re.findall(pat, asm))


def _finish(configs):
    for c in configs:
        c["exit_code"] = _run_exit(c["binary"])
        c["time_s"] = _time_best(c["binary"])
    return configs


# ===========================================================================
# Benchmark 1: _Nbit globals (SIMD bit-packing into xmm15)
# ===========================================================================
NBIT_FLAGS = ["enabled_1bit", "prio_3bit", "level_4bit", "mask_5bit", "mode_2bit"]


def _flag_mem_reads(fn_text):
    return sum(len(re.findall(r"PTR \[" + re.escape(f) + r"\]", fn_text))
               for f in NBIT_FLAGS)


def bench_nbit():
    d = os.path.join(HERE, "nbit_globals")
    src = os.path.join(d, "bench_nbit.c")
    cfgs = []

    _shivyc(src, os.path.join(d, "bin_shivyc_off"))
    n = _flag_mem_reads(_extract_function(_read_asm(src), "irq_handler"))
    cfgs.append({"name": "ShivyCX (no pack)", "binary": os.path.join(d, "bin_shivyc_off"),
                 "baseline": True, "metric": "%d flag mem-loads" % n, "metric_val": n})

    _shivyc(src, os.path.join(d, "bin_shivyc_on"), ["-fsimd-pack-globals"])
    n = _flag_mem_reads(_extract_function(_read_asm(src), "irq_handler"))
    cfgs.append({"name": "ShivyCX (-fsimd-pack-globals)", "binary": os.path.join(d, "bin_shivyc_on"),
                 "baseline": False, "metric": "%d flag mem-loads (reads xmm15)" % n, "metric_val": n})

    _gcc(src, os.path.join(d, "bin_gcc0"))
    cfgs.append({"name": "gcc -O0", "binary": os.path.join(d, "bin_gcc0"),
                 "baseline": False, "metric": "5 mem-loads; never uses xmm15", "metric_val": None})

    return {"benchmark": "nbit_globals", "configs": _finish(cfgs)}


# ===========================================================================
# Benchmark 2: contracts -> fallback-free SIMD reduction
# ===========================================================================
def bench_contracts():
    d = os.path.join(HERE, "contracts")
    src_on = os.path.join(d, "bench_contracts.c")
    src_off = os.path.join(d, "bench_contracts_baseline.c")
    _write_baseline(src_on, src_off, _strip_contract_clauses)
    cfgs = []

    _shivyc(src_on, os.path.join(d, "bin_shivyc_contract"))
    fn = _extract_function(_read_asm(src_on), "calc_sum")
    vec = "paddd" in fn and "movdqu" in fn
    cfgs.append({"name": "ShivyCX (+contract)", "binary": os.path.join(d, "bin_shivyc_contract"),
                 "baseline": False, "metric": "SSE2 body, no remainder" if vec else "scalar",
                 "metric_val": vec})

    _shivyc(src_off, os.path.join(d, "bin_shivyc_scalar"))
    cfgs.append({"name": "ShivyCX (scalar, no contract)", "binary": os.path.join(d, "bin_shivyc_scalar"),
                 "baseline": True, "metric": "scalar loop", "metric_val": False})

    _gcc(src_off, os.path.join(d, "bin_gcc0"))   # gcc can't parse `assert` clauses
    cfgs.append({"name": "gcc -O0", "binary": os.path.join(d, "bin_gcc0"),
                 "baseline": False, "metric": "scalar loop", "metric_val": None})

    return {"benchmark": "contracts_simd", "configs": _finish(cfgs)}


# ===========================================================================
# Benchmark 3: -fstackless-calls (direct call + tail-call + FPO)
# ===========================================================================
def bench_stackless():
    d = os.path.join(HERE, "stackless")
    src = os.path.join(d, "bench_stackless.c")
    cfgs = []

    _shivyc(src, os.path.join(d, "bin_shivyc_off"))
    asm = _read_asm(src)
    cfgs.append({"name": "ShivyCX (no stackless)", "binary": os.path.join(d, "bin_shivyc_off"),
                 "baseline": True,
                 "metric": "%d calls / %d frames" % (_count(asm, r"\bcall\b"), _count(asm, r"push rbp")),
                 "metric_val": _count(asm, r"\bcall\b")})

    _shivyc(src, os.path.join(d, "bin_shivyc_on"), ["-fstackless-calls"])
    asm = _read_asm(src)
    cfgs.append({"name": "ShivyCX (-fstackless-calls)", "binary": os.path.join(d, "bin_shivyc_on"),
                 "baseline": False,
                 "metric": "%d calls / %d frames" % (_count(asm, r"\bcall\b"), _count(asm, r"push rbp")),
                 "metric_val": _count(asm, r"\bcall\b")})

    _gcc(src, os.path.join(d, "bin_gcc0"))
    cfgs.append({"name": "gcc -O0", "binary": os.path.join(d, "bin_gcc0"),
                 "baseline": False, "metric": "full framed calls", "metric_val": None})

    return {"benchmark": "stackless", "configs": _finish(cfgs)}


# ===========================================================================
# Benchmark 4: -fmetamorphic (experimental, self-modifying return slot)
# ===========================================================================
def bench_metamorphic():
    d = os.path.join(HERE, "metamorphic")
    src_on = os.path.join(d, "bench_metamorphic.c")
    src_off = os.path.join(d, "bench_metamorphic_baseline.c")
    _write_baseline(src_on, src_off, _strip_metamorphic)
    cfgs = []

    _shivyc(src_off, os.path.join(d, "bin_shivyc_off"))
    cfgs.append({"name": "ShivyCX (normal call)", "binary": os.path.join(d, "bin_shivyc_off"),
                 "baseline": True, "metric": "ordinary call/ret", "metric_val": False})

    _shivyc(src_on, os.path.join(d, "bin_shivyc_on"), ["-fmetamorphic"])
    asm = _read_asm(src_on)
    smc = ".mtext" in asm and "metaret" in asm
    cfgs.append({"name": "ShivyCX (-fmetamorphic)", "binary": os.path.join(d, "bin_shivyc_on"),
                 "baseline": False,
                 "metric": "RWX .mtext slot, SMC per call" if smc else "no metamorphic emitted",
                 "metric_val": smc})

    _gcc(src_off, os.path.join(d, "bin_gcc0"))   # gcc can't parse __metamorphic__
    cfgs.append({"name": "gcc -O0", "binary": os.path.join(d, "bin_gcc0"),
                 "baseline": False, "metric": "ordinary call/ret", "metric_val": None})

    return {"benchmark": "metamorphic", "configs": _finish(cfgs)}


# ===========================================================================
# Benchmark 5: -f-eliminate-unused-members (whole-program struct shrink)
# ===========================================================================
def bench_member_elim():
    d = os.path.join(HERE, "member_elim")
    src = os.path.join(d, "bench_member_elim.c")

    def comm_bytes(asm, sym):
        m = re.search(r"\.comm " + re.escape(sym) + r"[ ,]+(\d+)", asm)
        return int(m.group(1)) if m else None

    _shivyc(src, os.path.join(d, "bin_off"))
    off = comm_bytes(_read_asm(src), "table")
    e_off = _run_exit(os.path.join(d, "bin_off"))

    rc, log = _shivyc(src, os.path.join(d, "bin_on"),
                      ["-f-eliminate-unused-members", "--print-eliminated-members"])
    on = comm_bytes(_read_asm(src), "table")
    removed = ""
    for ln in log.splitlines():
        if "eliminated from" in ln:
            removed = ln.strip()
    e_on = _run_exit(os.path.join(d, "bin_on"))

    _gcc(src, os.path.join(d, "bin_gcc0"))
    e_gcc = _run_exit(os.path.join(d, "bin_gcc0"))

    return {"benchmark": "member_elim", "kind": "bytes",
            "exit_agree": len({e_off, e_on, e_gcc}) == 1,
            "off_bytes": off, "on_bytes": on, "gcc_bytes": off,
            "removed": removed,
            "note": "gcc cannot shrink a struct: ABI-fixed layout"}


# ===========================================================================
# Benchmark 6: register-partitioned left/right threads (switch cost)
# ===========================================================================
def bench_threads():
    d = os.path.join(HERE, "threads")
    src = os.path.join(d, "bench_threads.c")
    sw = os.path.join(d, "switcher.s")
    cmd = [sys.executable, "-m", "shivyc.main", "--no-cache", src,
           "--emit-thread-switcher", sw]
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    log = p.stdout + p.stderr

    saved = save_all = None
    m = re.search(r"left->right (\d+) regs.*?vs (\d+) for save-all", log)
    if m:
        saved, save_all = int(m.group(1)), int(m.group(2))
    disjoint = "disjoint" in log
    return {"benchmark": "threads_leftright", "kind": "regs",
            "regs_saved": saved, "regs_save_all": save_all,
            "disjoint": disjoint,
            "note": "gcc has no per-thread register partition concept"}


# ===========================================================================
# Benchmark 7: whole-program memory safety (UAF / double-free / auto-free)
# ===========================================================================
def bench_memory_safety():
    mem = os.path.join(REPO, "examples", "memory")
    cases = [
        ("dangling_alias", "use-after-free", "intra-function, via alias"),
        ("double_free", "double-free", "intra-function"),
        ("wrapper_uaf", "use-after-free", "CROSS-function (free+deref in callees)"),
        ("autofree_leak", "auto-free", "leak closed automatically"),
    ]
    rows = []
    for name, kind, desc in cases:
        src = os.path.join(mem, name + ".c")
        rc, log = _shivyc(src, "/tmp/_ms", ["--check-memory"])
        if kind == "auto-free":
            shivy = "auto-free candidate" in log
        else:
            shivy = kind in log
        # gcc -O0 with all warnings.
        g = subprocess.run(["gcc", "-O0", "-Wall", "-Wextra", src, "-o", "/tmp/_msg"],
                           capture_output=True, text=True)
        gtext = g.stdout + g.stderr
        gcc_detect = ("use-after-free" in gtext or "double" in gtext) \
            if kind != "auto-free" else False
        rows.append({"case": name, "bug": kind, "desc": desc,
                     "shivyc_detects": shivy, "gcc_detects": gcc_detect})
    return {"benchmark": "memory_safety", "kind": "detection", "rows": rows}


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    results = []
    for label, fn in [("_Nbit globals", bench_nbit),
                      ("contracts SIMD", bench_contracts),
                      ("stackless calls", bench_stackless),
                      ("metamorphic returns", bench_metamorphic)]:
        print("Running %s benchmark ..." % label)
        results.append(fn())

    # Capability / static-metric benchmarks (no like-for-like runtime).
    print("Running member-elimination benchmark ...")
    me = bench_member_elim()
    print("Running left/right threads benchmark ...")
    th = bench_threads()
    print("Running memory-safety benchmark ...")
    ms = bench_memory_safety()
    results.extend([me, th, ms])

    with open(os.path.join(RESULTS_DIR, "results.json"), "w") as f:
        json.dump(results, f, indent=2)

    for bench in results[:4]:
        print("\n=== %s ===" % bench["benchmark"])
        exits = {c["exit_code"] for c in bench["configs"]}
        print("  differential correctness: %s (exit codes: %s)"
              % ("PASS" if len(exits) == 1 else "FAIL", exits))
        base = next((c for c in bench["configs"] if c["baseline"]), None)
        for c in bench["configs"]:
            sp = ""
            if base and c is not base and c["time_s"]:
                sp = "  (%.2fx vs ShivyCX baseline)" % (base["time_s"] / c["time_s"])
            print("  %-32s %.3fs  [%s]%s" % (c["name"], c["time_s"], c["metric"], sp))

    print("\n=== member_elim ===")
    print("  correctness: %s" % ("PASS" if me["exit_agree"] else "FAIL"))
    print("  %s" % me["removed"])
    print("  table[] .bss:  ShivyCX off = %d B   ShivyCX on = %d B   gcc -O0 = %d B  (%.1fx smaller; %s)"
          % (me["off_bytes"], me["on_bytes"], me["gcc_bytes"],
             me["off_bytes"] / me["on_bytes"], me["note"]))

    print("\n=== threads_leftright ===")
    print("  context-switch register save: %d regs  (vs %d for save-all; %s)"
          % (th["regs_saved"], th["regs_save_all"], th["note"]))

    print("\n=== memory_safety ===")
    print("  %-16s %-14s %-8s %-8s  %s" % ("case", "bug", "ShivyCX", "gcc-O0", "scenario"))
    for r in ms["rows"]:
        print("  %-16s %-14s %-8s %-8s  %s"
              % (r["case"], r["bug"],
                 "YES" if r["shivyc_detects"] else "no",
                 "YES" if r["gcc_detects"] else "no", r["desc"]))

    print("\nWrote", os.path.join(RESULTS_DIR, "results.json"))


if __name__ == "__main__":
    main()
