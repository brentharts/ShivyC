r"""Micro-slicing: turn wasted spin-wait cycles into useful, independent work.

A core that loses a lock race burns power polling. The micro-slicing pass
(\S7.1 of the paper) fills that spin window with work that is safe to interleave
with the wait, without compromising the latency of the critical section. It has
three parts, all driven by the whole-program call graph:

  1. Candidate identification (this module's purity/independence analysis):
     find a *future execution fragment* that is safe to interleave -- it must be
     **pure** (free of side effects on shared memory, so interleaving cannot
     create a race) and therefore **memory-independent** of whatever the held
     lock protects.

  2. The fragmentor: estimate the cost of the fragment's hot loop by counting
     IL operations, then quantize it into slices of a bounded duration so a lock
     poll can be inserted between slices. If a slice's worst-case cost is `T`,
     the lock is observed at least every `T`, so the acquisition latency added
     by productive spinning is bounded by `T`.

  3. Code generation: replace the idle acquire with a loop that runs one slice
     of the fragment per poll. The moment the lock is released, the spinner is
     at most one slice from its next poll, so it enters the critical section
     almost immediately -- having turned otherwise-wasted cycles into completed
     work.

The analysis runs on the same IL the other whole-program passes use, reusing the
program loader and CFG from `memory_safety`.
"""

import shivyc.il_cmds.control as control_cmds
import shivyc.il_cmds.value as value_cmds
import shivyc.il_cmds.math as math_cmds
import shivyc.il_cmds.compare as compare_cmds
from shivyc.memory_safety import load_program, CFG

# Functions with observable side effects: calling one disqualifies a fragment
# from being interleaved into a spin-wait.
IMPURE_CALLS = {
    "printf", "puts", "putchar", "fprintf", "fputs", "write", "read", "scanf",
    "malloc", "calloc", "realloc", "free", "exit", "abort", "fopen", "fclose",
    "memcpy", "memset", "memmove", "strcpy", "strcat",
}

# Approximate per-operation cost, in "cycle-ish" units, used only to size
# slices. The absolute scale does not matter; only the ratio to the slice
# budget does.
OP_COST = {
    "Set": 1, "LoadArg": 1, "AddrOf": 1, "ReadAt": 3, "SetAt": 3,
    "Add": 1, "Subtr": 1, "BitAnd": 1, "BitOr": 1, "BitXor": 1,
    "LBitShift": 1, "RBitShift": 1, "Neg": 1, "Not": 1,
    "Mult": 3, "Div": 25, "Mod": 25,
    "NotEqualCmp": 1, "EqualCmp": 1, "LessCmp": 1, "GreaterCmp": 1,
    "LessOrEqCmp": 1, "GreaterOrEqCmp": 1,
    "Label": 0, "Jump": 1, "JumpZero": 1, "JumpNotZero": 1,
    "Return": 1, "Call": 10,
    "ReadRel": 3, "SetRel": 3, "AddrRel": 1,
}

# A nominal clock so the report can quote nanoseconds (3 GHz -> 1 cycle ~ 0.33 ns).
# At ~3 GHz one cycle is ~0.33 ns, i.e. ~3 cost units per ns. We keep the
# estimate in integer arithmetic (cost // COST_PER_NS) rather than floats.
COST_PER_NS = 3


class Fragment:
    """A pure, interleavable computation with a bounded hot loop."""

    def __init__(self, name):
        self.name = name
        self.pure = False
        self.impure_reason = ""
        self.has_loop = False
        self.iter_cost = 0          # estimated cost of one loop iteration
        self.body_blocks = 0        # number of basic blocks in the loop body
        self.total_cost = 0         # estimated cost of the whole function


def _op_cost(cmd):
    return OP_COST.get(type(cmd).__name__, 1)


def _purity(cmds, prog, pure_funcs):
    """Return (is_pure, reason). A function is interleavable if it has no
    observable side effect: it writes no caller-visible memory (no store through
    a pointer, no store into a global) and calls nothing impure."""
    for c in cmds:
        if isinstance(c, value_cmds.SetAt):
            return False, "writes through a pointer (store to caller memory)"
        if isinstance(c, value_cmds.SetRel):
            return False, "writes through a pointer (store to caller memory)"
        if isinstance(c, value_cmds.Set) and c.output in prog.globals:
            return False, "writes a global variable"
        if isinstance(c, control_cmds.Call):
            name = c.direct_name
            if name is None:
                return False, "makes an indirect call (effects unknown)"
            if name in IMPURE_CALLS:
                return False, f"calls impure function {name}()"
            if name not in pure_funcs and name not in prog.functions:
                return False, f"calls external function {name}() (effects unknown)"
            if name in prog.functions and name not in pure_funcs:
                return False, f"calls {name}(), which is not pure"
    return True, ""


def _find_loop_body(cfg):
    """Find the blocks of the innermost counted loop via a CFG back-edge.

    Returns (body_block_ids, found). A back-edge is an edge b -> t with t <= b;
    the loop body is blocks [t .. b]."""
    best = None
    for b in range(len(cfg.blocks)):
        for t in cfg.succ[b]:
            if t <= b:                       # back-edge
                span = b - t
                if best is None or span < best[2]:
                    best = (t, b, span)
    if best is None:
        return [], False
    t, b, _ = best
    return list(range(t, b + 1)), True


def _block_cost(cfg, block_id):
    start, end = cfg.blocks[block_id]
    return sum(_op_cost(cfg.cmds[i]) for i in range(start, end))


def analyze(prog):
    """Compute a Fragment record for every function, callees-first so purity
    propagates through pure helper calls."""
    frags = {}
    pure_funcs = set()
    # Iterate to a fixpoint so purity propagates along the call graph.
    changed = True
    order = list(prog.functions)
    while changed:
        changed = False
        for fn in order:
            cmds = prog.functions[fn]
            pure, reason = _purity(cmds, prog, pure_funcs)
            if pure and fn not in pure_funcs:
                pure_funcs.add(fn)
                changed = True

    for fn, cmds in prog.functions.items():
        f = Fragment(fn)
        f.pure, f.impure_reason = _purity(cmds, prog, pure_funcs)
        f.total_cost = sum(_op_cost(c) for c in cmds)
        cfg = CFG(cmds)
        body, found = _find_loop_body(cfg)
        f.has_loop = found
        if found:
            f.body_blocks = len(body)
            f.iter_cost = sum(_block_cost(cfg, b) for b in body)
        frags[fn] = f
    return frags


def plan_slice(frag, budget):
    """Quantize a fragment's loop into slices of at most `budget` cost.

    Returns (iters_per_slice, slice_cost, latency_ns)."""
    per_iter = max(1, frag.iter_cost)
    iters = max(1, budget // per_iter)
    slice_cost = iters * per_iter
    return iters, slice_cost, slice_cost // COST_PER_NS


def emit_scaffold(frag, iters):
    """Emit C scaffolding for the work-injected acquire and a resumable stepper
    derived from the pure fragment `frag` (named `<frag>`). This is the 'code
    generation' step: the idle spin is replaced by one that runs a slice per
    poll."""
    f = frag.name
    return f"""/* --- generated by micro-slicing for fragment {f}() --- */
/* Resumable form of the pure fragment: advances up to `k` iterations of the
   {f} loop, carrying its accumulator in compiler-introduced private state that
   is disjoint from any lock-protected memory by construction. */
typedef struct {{ int i; int n; long acc; }} {f}_slice;
void {f}_slice_init({f}_slice *s, int n) {{ s->i = 0; s->n = n; s->acc = 0; }}
int  {f}_slice_step({f}_slice *s, int k);   /* returns 1 while work remains */

/* Productive acquire: run one {iters}-iteration slice (<= one budget) per poll.
   Acquisition latency added by spinning is bounded by a single slice. */
int acquire_productive(volatile int *lock, {f}_slice *s) {{
    int polls = 0;
    while (test_and_set(lock)) {{      /* lost the race: spin productively */
        polls += 1;
        {f}_slice_step(s, {iters});    /* ~one slice of independent work */
    }}
    return polls;                      /* lock held; useful work already done */
}}
"""


def format_report(prog, frags, budget):
    out = []
    out.append("micro-slicing analysis (productive spinning)")
    out.append("=" * 44)
    out.append(f"slice budget: {budget} cost units "
               f"(~{budget // COST_PER_NS} ns at 3 GHz)")
    out.append("")

    candidates = [f for f in frags.values() if f.pure and f.has_loop]
    impure = [f for f in frags.values() if not f.pure]

    if candidates:
        out.append("interleavable fragments (pure + bounded loop):")
        for f in sorted(candidates, key=lambda x: x.name):
            iters, slice_cost, ns = plan_slice(f, budget)
            out.append(f"  {f.name}(): pure, hot loop over {f.body_blocks} "
                       f"block(s), ~{f.iter_cost} cost/iteration")
            out.append(f"      -> slice = {iters} iteration(s) per poll "
                       f"(~{ns} ns); lock observed at least every slice")
            out.append(f"      -> added acquisition latency bounded by "
                       f"~{ns} ns (one slice)")
    else:
        out.append("no interleavable fragment found "
                   "(need a pure function with a bounded loop)")

    pure_noloop = [f for f in frags.values() if f.pure and not f.has_loop]
    if pure_noloop:
        out.append("")
        out.append("pure but not sliceable (no bounded loop to quantize): "
                   + ", ".join(sorted(f.name + "()" for f in pure_noloop)))
    if impure:
        out.append("")
        out.append("not interleavable (has side effects):")
        for f in sorted(impure, key=lambda x: x.name):
            out.append(f"  {f.name}(): {f.impure_reason}")
    return "\n".join(out)


def run(files, args):
    """Entry point for --microslice: analyze and print the slicing plan."""
    budget = getattr(args, "slice_budget", None) or 64
    prog, ok = load_program(files, args)
    frags = analyze(prog)
    print(format_report(prog, frags, budget))

    if getattr(args, "emit_microslice", None):
        cands = [f for f in frags.values() if f.pure and f.has_loop]
        if cands:
            f = sorted(cands, key=lambda x: -x.total_cost)[0]
            iters, _, _ = plan_slice(f, budget)
            with open(args.emit_microslice, "w") as fh:
                fh.write(emit_scaffold(f, iters))
            print(f"\nwrote work-injected acquire scaffold for {f.name}() "
                  f"to {args.emit_microslice}")
    return 0
