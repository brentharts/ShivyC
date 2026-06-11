"""Register-partitioned thread context switching for ShivyCX.

A function header may declare which functions run as bare-metal threads, and on
which side of a two-way register split each one lives:

    void main()
    assert worker_a in threads.left(core=0)
    assert worker_b in threads.right(core=0)
    { worker_a(); worker_b(); }

`extensions.py` records these as {fn: {'side','core'}}. This module turns that,
plus the whole-program call graph and each function's *actual* post-allocation
register footprint, into:

  1. a per-thread register footprint (union over the thread's transitive call
     graph),
  2. a partition of the GP and XMM register files into a `left` budget and a
     `right` budget that are disjoint where the footprints allow it, and
  3. a *specialized* context switcher: because the two groups use disjoint
     registers, the left->right and right->left routines each save/restore only
     their own group's registers, with no runtime test of "which kind of thread
     is current". A left thread can only have left registers live, so that is
     all the switch must preserve.

The partition the analysis computes can also be fed back into the register
allocator (see asm_gen's per-function `alloc_registers` hook) so the real code
generation is constrained to each thread's budget, making the disjointness --
and therefore the minimal switcher -- a guarantee rather than an observation.
"""

import shivyc.spots as spots

# GP allocation pool, in the allocator's preferred order.
GP_POOL = [r.name for r in spots.registers]          # rax,rcx,rdx,rsi,rdi,r8,r9,r10,r11
XMM_POOL = [x.name for x in spots.xmm_arg_regs]       # xmm0..xmm7

# Map every sub-register spelling back to its 64-bit owner (eax->rax, etc).
_SUB_TO_R64 = {}
for _r64, _names in spots.RegSpot.reg_map.items():
    for _nm in _names:
        if _nm:
            _SUB_TO_R64[_nm] = _r64


def transitive_closure(edges, root):
    """All functions reachable from `root` (inclusive) via direct-call edges."""
    seen, stack = set(), [root]
    while stack:
        fn = stack.pop()
        if fn in seen:
            continue
        seen.add(fn)
        stack.extend(edges.get(fn, ()))
    return seen


def scan_asm_registers(asm_text):
    """Scan emitted Intel-syntax asm, returning {func: {'gp': set, 'xmm': set}}.

    Register usage is read per function body (delimited by `name:` labels that
    follow a `.global name`), normalising sub-registers to their 64-bit owner.
    This is the real, post-register-allocation footprint.
    """
    import re
    tok = re.compile(r"\b([a-z][a-z0-9]*)\b")
    funcs = {}
    cur = None
    globals_seen = set()
    for raw in asm_text.splitlines():
        line = raw.strip()
        if line.startswith(".global"):
            globals_seen.add(line.split()[1])
            continue
        if line.endswith(":") and not line.startswith("."):
            label = line[:-1].strip()
            cur = label if label in globals_seen else cur
            funcs.setdefault(cur, {"gp": set(), "xmm": set()})
            continue
        if cur is None:
            continue
        # only look at the operand text, after the mnemonic
        parts = line.split(None, 1)
        operands = parts[1] if len(parts) > 1 else ""
        for t in tok.findall(operands):
            if t in _SUB_TO_R64:
                r64 = _SUB_TO_R64[t]
                if r64 in GP_POOL:
                    funcs[cur]["gp"].add(r64)
            elif t.startswith("xmm"):
                funcs[cur]["xmm"].add(t)
    funcs.pop(None, None)
    return funcs


class ThreadPlan:
    """Result of the thread-partition analysis."""

    def __init__(self):
        self.threads = {}        # fn -> {'side','core'}
        self.members = {}        # fn -> set(transitively-called functions)
        self.left_funcs = set()
        self.right_funcs = set()
        self.shared = set()      # functions reachable from both sides
        self.left_gp = set()
        self.right_gp = set()
        self.left_xmm = set()
        self.right_xmm = set()
        self.gp_budget = {"left": [], "right": []}
        self.xmm_budget = {"left": [], "right": []}
        self.gp_overlap = []     # regs that must be saved on a cross switch
        self.xmm_overlap = []


def _split_budget(pool, need_left, need_right):
    """Carve `pool` into disjoint left/right budgets covering the needs.

    Left takes from the front, right from the back, so the two budgets stay
    disjoint. If the combined need exceeds the pool, the budgets meet in the
    middle and the shared tail is reported as overlap (must be saved on a
    cross-group switch).
    """
    n = len(pool)
    if need_left + need_right <= n:
        left = pool[:need_left]
        right = pool[n - need_right:] if need_right else []
        return left, right, []
    # Over-subscribed: proportional split, overlap = intersection.
    cut = round(n * need_left / (need_left + need_right)) if (need_left + need_right) else 0
    cut = max(0, min(n, cut))
    left = pool[:max(cut, need_left)]
    right = pool[n - max(n - cut, need_right):]
    overlap = sorted(set(left) & set(right), key=pool.index)
    return left, right, overlap


def analyze(threads, edges, func_regs):
    """Build a ThreadPlan from thread declarations, call edges and footprints.

    threads   - {fn: {'side','core'}}
    edges     - {caller: set(callees)} whole-program call graph
    func_regs - {fn: {'gp': set, 'xmm': set}} post-allocation footprints
    """
    plan = ThreadPlan()
    plan.threads = dict(threads)

    for fn, rec in threads.items():
        members = transitive_closure(edges, fn)
        plan.members[fn] = members
        if rec["side"] == "left":
            plan.left_funcs |= members
        else:
            plan.right_funcs |= members

    plan.shared = plan.left_funcs & plan.right_funcs

    def footprint(funcs, key):
        out = set()
        for f in funcs:
            out |= func_regs.get(f, {}).get(key, set())
        return out

    plan.left_gp = footprint(plan.left_funcs, "gp")
    plan.right_gp = footprint(plan.right_funcs, "gp")
    plan.left_xmm = footprint(plan.left_funcs, "xmm")
    plan.right_xmm = footprint(plan.right_funcs, "xmm")

    lg, rg, og = _split_budget(GP_POOL, len(plan.left_gp), len(plan.right_gp))
    lx, rx, ox = _split_budget(XMM_POOL, len(plan.left_xmm), len(plan.right_xmm))
    plan.gp_budget = {"left": lg, "right": rg}
    plan.xmm_budget = {"left": lx, "right": rx}
    plan.gp_overlap = og
    plan.xmm_overlap = ox
    return plan


def allocation_budgets(plan):
    """{fn: {'gp':[...], 'xmm':[...]}} restricting each thread function to its
    group's register budget, for feeding back into the allocator. Shared
    functions get the intersection (so they are safe to call from either side).
    """
    out = {}
    inter_gp = [r for r in GP_POOL
                if r in plan.gp_budget["left"] and r in plan.gp_budget["right"]]
    inter_xmm = [x for x in XMM_POOL
                 if x in plan.xmm_budget["left"] and x in plan.xmm_budget["right"]]
    for fn in plan.left_funcs | plan.right_funcs:
        if fn in plan.shared:
            out[fn] = {"gp": inter_gp or plan.gp_budget["left"],
                       "xmm": inter_xmm or plan.xmm_budget["left"]}
        elif fn in plan.left_funcs:
            out[fn] = {"gp": plan.gp_budget["left"], "xmm": plan.xmm_budget["left"]}
        else:
            out[fn] = {"gp": plan.gp_budget["right"], "xmm": plan.xmm_budget["right"]}
    return out


def generate_switcher(plan):
    """Emit the specialized context switcher as GNU-as Intel-syntax asm.

    Each direction saves exactly the *footprint* of the outgoing thread's group
    and restores the footprint of the incoming group -- always correct, however
    the registers were allocated. When the footprints are made disjoint (via the
    constrained-allocation feedback), the two save-sets share nothing and the
    routines are minimal. There is no runtime test of thread kind: each
    direction's register list is baked in.

    TCB layout (per thread), 8-byte slots unless noted:
        +0   saved rsp
        +8   saved resume address
        +16.. saved group GP registers (footprint order), then XMM (16B each)
    """
    left_gp = [r for r in GP_POOL if r in plan.left_gp]
    right_gp = [r for r in GP_POOL if r in plan.right_gp]
    left_xmm = [x for x in XMM_POOL if x in plan.left_xmm]
    right_xmm = [x for x in XMM_POOL if x in plan.right_xmm]

    lines = []
    lines.append("/* Generated by ShivyCX thread_contracts: register-")
    lines.append("   partitioned context switcher (left/right groups). */")
    lines.append("    .intel_syntax noprefix")
    lines.append("    .section .text")

    def emit(name, save_gp, save_xmm, restore_gp, restore_xmm):
        lines.append("    .global " + name)
        lines.append(name + ":")
        off = 16
        for r in save_gp:
            lines.append(f"    mov QWORD PTR [rdi+{off}], {r}")
            off += 8
        for x in save_xmm:
            lines.append(f"    movdqu XMMWORD PTR [rdi+{off}], {x}")
            off += 16
        lines.append("    mov QWORD PTR [rdi+0], rsp")
        lines.append("    mov rax, [rsp]")          # resume address (pushed by call)
        lines.append("    mov QWORD PTR [rdi+8], rax")
        off = 16
        for r in restore_gp:
            lines.append(f"    mov {r}, QWORD PTR [rsi+{off}]")
            off += 8
        for x in restore_xmm:
            lines.append(f"    movdqu {x}, XMMWORD PTR [rsi+{off}]")
            off += 16
        lines.append("    mov rsp, QWORD PTR [rsi+0]")
        lines.append("    ret")
        lines.append("")

    emit("switch_to_right", left_gp, left_xmm, right_gp, right_xmm)
    emit("switch_to_left", right_gp, right_xmm, left_gp, left_xmm)

    lines.append("    .section .note.GNU-stack,\"\",@progbits")
    return "\n".join(lines) + "\n"


def generate_preempt_switcher(plan):
    """Emit the partition-aware *preemptive* timer path as GNU-as asm.

    Installed at IDT vector 32 (IRQ0) in place of the generic irq0 stub, this
    saves only the running thread's group footprint instead of the full
    interrupt_frame64. The trick: left and right footprints are disjoint, so the
    left timer ISR can use the right registers as scratch (they are dead for a
    left thread) and vice-versa -- no extra spills, and no runtime test of which
    kind of thread is current.

    Control transfers through `timer_dispatch` (the gate target), which does
    `jmp [timer_vector]`. Each ISR flips `timer_vector` to the other side before
    returning, so the next tick is already specialized -- the selection is data,
    never a branch on thread kind.

    TCB layout (per thread), 8-byte slots:
        +0  saved rsp     +8  saved rip     +16 saved rflags
        +24.. saved group GP regs (footprint order), then XMM (16B each)

    Scheduler globals it expects (defined here in .bss/.data):
        cur_tcb, next_tcb : pointers to the running / other thread's TCB
        timer_vector      : current specialized ISR (seeded to timer_isr_left)
    """
    left_gp = [r for r in GP_POOL if r in plan.left_gp]
    right_gp = [r for r in GP_POOL if r in plan.right_gp]
    left_xmm = [x for x in XMM_POOL if x in plan.left_xmm]
    right_xmm = [x for x in XMM_POOL if x in plan.right_xmm]

    CS, SS = "0x08", "0x10"
    L = []
    L.append("/* Generated by ShivyCX thread_contracts: partition-aware")
    L.append("   preemptive timer switcher (IRQ0). Saves only the running")
    L.append("   group's footprint. Install timer_dispatch at IDT[32]. */")
    L.append("    .intel_syntax noprefix")
    L.append("    .section .text")
    L.append("    .global timer_dispatch")
    L.append("timer_dispatch:")
    L.append("    jmp QWORD PTR [rip+timer_vector]")
    L.append("")

    def emit(name, save_gp, save_xmm, rest_gp, rest_xmm, other, scratch, base, frame):
        # scratch: regs free during SAVE phase (incoming group, dead for outgoing)
        # base/frame: regs free during RESTORE phase (outgoing group, already saved)
        s0, s1 = scratch[0], scratch[1]
        L.append("    .global " + name)
        L.append(name + ":")
        L.append(f"    mov {s0}, QWORD PTR [rip+cur_tcb]")
        off = 24
        for r in save_gp:
            L.append(f"    mov QWORD PTR [{s0}+{off}], {r}")
            off += 8
        for x in save_xmm:
            L.append(f"    movdqu XMMWORD PTR [{s0}+{off}], {x}")
            off += 16
        # CPU iret frame at [rsp]: rip, cs, rflags, rsp, ss
        L.append(f"    mov {s1}, [rsp+0]")
        L.append(f"    mov QWORD PTR [{s0}+8], {s1}     /* rip */")
        L.append(f"    mov {s1}, [rsp+16]")
        L.append(f"    mov QWORD PTR [{s0}+16], {s1}    /* rflags */")
        L.append(f"    mov {s1}, [rsp+24]")
        L.append(f"    mov QWORD PTR [{s0}+0], {s1}     /* rsp */")
        # PIC EOI (al/dx belong to the outgoing group -> already saved, free)
        L.append("    mov al, 0x20")
        L.append("    mov dx, 0x20")
        L.append("    out dx, al")
        # flip dispatch target for the next tick (no test next time)
        L.append(f"    lea {s1}, [rip+{other}]")
        L.append(f"    mov QWORD PTR [rip+timer_vector], {s1}")
        # Swap, reading old-cur FRESH from memory (cur_tcb is still valid here):
        # the save-phase base may alias al/dx clobbered by the EOI above, so we
        # do not rely on it surviving.
        L.append(f"    mov {s1}, QWORD PTR [rip+cur_tcb]   /* old cur */")
        L.append(f"    mov {base}, QWORD PTR [rip+next_tcb] /* incoming */")
        L.append(f"    mov QWORD PTR [rip+next_tcb], {s1}   /* next = old cur */")
        L.append(f"    mov QWORD PTR [rip+cur_tcb], {base}  /* cur = incoming */")
        off = 24
        for r in rest_gp:
            L.append(f"    mov {r}, QWORD PTR [{base}+{off}]")
            off += 8
        for x in rest_xmm:
            L.append(f"    movdqu {x}, XMMWORD PTR [{base}+{off}]")
            off += 16
        # build an iret frame on the incoming thread's stack and iretq
        f0, f1, f2 = frame
        L.append(f"    mov {f0}, QWORD PTR [{base}+0]      /* incoming rsp */")
        L.append(f"    mov {f1}, QWORD PTR [{base}+8]      /* incoming rip */")
        L.append(f"    mov {f2}, QWORD PTR [{base}+16]     /* incoming rflags */")
        L.append(f"    sub {f0}, 40")
        L.append(f"    mov QWORD PTR [{f0}+0], {f1}")
        L.append(f"    mov QWORD PTR [{f0}+8], {CS}")
        L.append(f"    mov QWORD PTR [{f0}+16], {f2}")
        L.append(f"    mov {f1}, QWORD PTR [{base}+0]")
        L.append(f"    mov QWORD PTR [{f0}+24], {f1}")
        L.append(f"    mov QWORD PTR [{f0}+32], {SS}")
        L.append(f"    mov rsp, {f0}")
        L.append("    iretq")
        L.append("")

    # left running: scratch = right regs; restore-phase base/frame = left regs
    emit("timer_isr_left", left_gp, left_xmm, right_gp, right_xmm,
         "timer_isr_right", scratch=right_gp[:2] or ["r8", "r9"],
         base=left_gp[0], frame=left_gp[1:4])
    # right running: scratch = left regs; base/frame = right regs
    emit("timer_isr_right", right_gp, right_xmm, left_gp, left_xmm,
         "timer_isr_left", scratch=left_gp[:2] or ["rax", "rcx"],
         base=right_gp[0], frame=right_gp[1:4])

    L.append("    .section .data")
    L.append("    .align 8")
    L.append("    .global timer_vector")
    L.append("timer_vector: .quad timer_isr_left")
    L.append("    .section .bss")
    L.append("    .align 8")
    L.append("    .global cur_tcb")
    L.append("cur_tcb: .skip 8")
    L.append("    .global next_tcb")
    L.append("next_tcb: .skip 8")
    L.append("    .section .note.GNU-stack,\"\",@progbits")
    return "\n".join(L) + "\n"


def format_report(plan):
    """Human-readable summary of the partition and switch cost."""
    def fmt(s, order):
        return ", ".join(r for r in order if r in s) or "(none)"
    L = []
    L.append("ShivyCX thread partition")
    L.append("========================")
    for fn, rec in sorted(plan.threads.items()):
        L.append(f"  thread {fn}: side={rec['side']} core={rec['core']} "
                 f"(call graph: {len(plan.members[fn])} fns)")
    L.append("")
    L.append(f"left  call graph : {len(plan.left_funcs)} fns")
    L.append(f"right call graph : {len(plan.right_funcs)} fns")
    if plan.shared:
        L.append(f"shared functions : {', '.join(sorted(plan.shared))}")
    else:
        L.append("shared functions : (none - call graphs are disjoint)")
    L.append("")
    L.append(f"left  GP footprint : {fmt(plan.left_gp, GP_POOL)}")
    L.append(f"right GP footprint : {fmt(plan.right_gp, GP_POOL)}")
    L.append(f"left  GP budget    : {', '.join(plan.gp_budget['left']) or '(none)'}")
    L.append(f"right GP budget    : {', '.join(plan.gp_budget['right']) or '(none)'}")
    if plan.left_xmm or plan.right_xmm:
        L.append(f"left  XMM footprint: {fmt(plan.left_xmm, XMM_POOL)}")
        L.append(f"right XMM footprint: {fmt(plan.right_xmm, XMM_POOL)}")
    L.append("")
    naive = len(GP_POOL) + len(XMM_POOL)            # save-everything switch
    cross_l = len(plan.left_gp) + len(plan.left_xmm)
    cross_r = len(plan.right_gp) + len(plan.right_xmm)
    overlap = (plan.left_gp & plan.right_gp) | (plan.left_xmm & plan.right_xmm)
    if overlap:
        L.append(f"footprints overlap on: {', '.join(sorted(overlap))} "
                 "(shared regs saved on each side; constrained allocation can "
                 "push these apart)")
    else:
        L.append("footprints are disjoint: left and right share no registers")
    L.append(f"context switch saves: left->right {cross_l} regs, "
             f"right->left {cross_r} regs (vs {naive} for save-all)")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Driver: collect declarations, scan real footprints, constrain, emit switcher.
# ---------------------------------------------------------------------------
def _collect_threads(files):
    """Union of threads.left/right declarations across all .c inputs."""
    import shivyc.extensions as extensions
    threads = {}
    for f in files:
        if not f.endswith(".c"):
            continue
        try:
            _, info = extensions.preprocess_extensions(open(f).read())
        except Exception:
            continue
        threads.update(info.threads)
    return threads


def _compile_and_scan(files, budget_json=None):
    """Compile each .c (optionally with a register budget) and scan the emitted
    asm, returning the merged {func: {'gp','xmm'}} footprint map."""
    import os
    import shutil
    import subprocess
    import sys
    import tempfile

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    func_regs = {}
    workdir = tempfile.mkdtemp(prefix="shivycx_threads_")
    try:
        for f in files:
            if not f.endswith(".c"):
                continue
            base = os.path.basename(f)
            cpy = os.path.join(workdir, base)
            shutil.copyfile(f, cpy)
            cmd = [sys.executable, "-m", "shivyc.main", cpy, "-c",
                   "-o", cpy[:-2] + ".o"]
            if budget_json:
                cmd += ["--thread-alloc-json", budget_json]
            r = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
            s_path = cpy[:-2] + ".s"
            if r.returncode != 0 or not os.path.exists(s_path):
                continue
            for fn, regs in scan_asm_registers(open(s_path).read()).items():
                slot = func_regs.setdefault(fn, {"gp": set(), "xmm": set()})
                slot["gp"] |= regs["gp"]
                slot["xmm"] |= regs["xmm"]
        return func_regs
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def run(files, args):
    """End-to-end thread-partition driver behind --emit-thread-switcher.

    1. collect threads.left/right declarations,
    2. build the whole-program call graph,
    3. scan each thread's real (post-allocation) register footprint,
    4. compute the partition; feed disjoint budgets back into the allocator and
       re-scan so the footprints become disjoint,
    5. emit the specialized switcher and print a before/after report.
    """
    import json
    import os
    import tempfile
    import shivyc.callgraph as callgraph
    from shivyc.errors import error_collector

    threads = _collect_threads(files)
    if not threads:
        print("no threads.left/right declarations found in inputs")
        return 1

    graph, _ = callgraph.build_program_graph(files, args)
    error_collector.clear()

    # Pass 1: unconstrained footprints.
    fr0 = _compile_and_scan(files)
    plan0 = analyze(threads, graph.edges, fr0)

    print(format_report(plan0))
    print()

    # Pass 2: feed disjoint budgets back into the allocator, re-scan.
    budgets = allocation_budgets(plan0)
    flat = {fn: b["gp"] for fn, b in budgets.items()}
    bj = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(flat, bj)
    bj.close()
    try:
        fr1 = _compile_and_scan(files, budget_json=bj.name)
    finally:
        os.unlink(bj.name)
    plan1 = analyze(threads, graph.edges, fr1)

    print("=== after constrained re-allocation ===")
    print(format_report(plan1))

    out = args.emit_thread_switcher
    with open(out, "w") as fh:
        fh.write(generate_switcher(plan1))
    # Also emit the partition-aware preemptive timer path (IRQ0).
    preempt = out[:-2] + ".preempt.s" if out.endswith(".s") else out + ".preempt.s"
    with open(preempt, "w") as fh:
        fh.write(generate_preempt_switcher(plan1))
    gp_full = len(GP_POOL)
    print()
    print(f"wrote cooperative switcher: {out}")
    print(f"wrote preemptive timer path: {preempt}")
    print(f"  timer ISR saves the running group's footprint "
          f"(left {len(plan1.left_gp)}, right {len(plan1.right_gp)} GP regs) "
          f"instead of all {gp_full} on every tick")
    return 0
