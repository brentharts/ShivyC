#!/usr/bin/env python3
"""Render the cross-runtime rpython benchmark results to PNG + PDF.

Reads benchmarks/results/rpython_results.json (written by
run_rpython_benchmarks.py) and writes, into the output directory (default
/tmp/shivyc_benchmarks, override with $BENCH_PLOT_DIR or argv[1]):

  * <benchmark>.png / .pdf  -- per benchmark, three panels: runtime (s),
                               peak memory (MB), and compile time (s).
  * summary_runtime.png/.pdf -- runtime of every benchmark, all backends,
                               normalised to CPython = 1 (log scale).
  * summary_memory.png/.pdf  -- peak RSS of every benchmark, all backends (MB).

Both PNG (for quick viewing) and PDF (vector, for the LaTeX report) are emitted
for every figure.
"""
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results", "rpython_results.json")

# Fixed backend order + colours so every figure reads consistently.
BACKENDS = ["cpython", "pypy3", "gcc", "selfhost"]
COLORS = {
    "cpython":  "#4C72B0",
    "pypy3":    "#DD8452",
    "gcc":      "#55A868",
    "selfhost": "#C44E52",
}
LABELS = {
    "cpython":  "CPython",
    "pypy3":    "PyPy3",
    "gcc":      "py2c+gcc",
    "selfhost": "py2c+ShivyCX\n(self-hosted)",
}


def _save(fig, out_dir, stem):
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(out_dir, stem + "." + ext),
                    bbox_inches="tight", dpi=130)
    plt.close(fig)


def _bar_panel(ax, title, names, values, colors, unit, logscale=False):
    y = np.arange(len(names))
    ax.barh(y, values, color=colors, height=0.6)
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=8)
    ax.invert_yaxis()
    ax.set_title(title, fontsize=10)
    ax.set_xlabel(unit, fontsize=8)
    if logscale:
        ax.set_xscale("log")
    vmax = max([v for v in values if v] or [1])
    for yi, v in zip(y, values):
        if v:
            ax.text(v, yi, "  %.3g" % v, va="center", fontsize=7)
    ax.set_xlim(0 if not logscale else None,
                vmax * (1.35 if not logscale else 3))
    ax.grid(axis="x", alpha=0.25)


def per_benchmark(bench, out_dir):
    name = bench["benchmark"]
    be = bench["backends"]
    present = [b for b in BACKENDS if b in be]

    rt_names = [LABELS[b] for b in present]
    rt_vals = [be[b]["runtime_s"] for b in present]
    rt_cols = [COLORS[b] for b in present]

    mem_vals = [be[b]["rss_kb"] / 1024.0 for b in present]

    comp = [b for b in present if be[b].get("compile_s")]
    comp_names = [LABELS[b] for b in comp]
    comp_vals = [be[b]["compile_s"] for b in comp]
    comp_cols = [COLORS[b] for b in comp]

    fig, axes = plt.subplots(1, 3, figsize=(11, 3.1))
    _bar_panel(axes[0], "Runtime", rt_names, rt_vals, rt_cols,
               "seconds (best of 5)", logscale=True)
    _bar_panel(axes[1], "Peak memory", rt_names, mem_vals, rt_cols, "MB")
    if comp_vals:
        _bar_panel(axes[2], "Compile time", comp_names, comp_vals, comp_cols,
                   "seconds")
    else:
        axes[2].axis("off")
    args = " ".join(bench.get("run_args", []))
    ok = "exit codes agree" if bench.get("exit_agree") else "EXIT MISMATCH"
    fig.suptitle("%s   (arg: %s -- %s)" % (name, args or "none", ok),
                 fontsize=12, y=1.04)
    fig.tight_layout()
    _save(fig, out_dir, name)


def summary_runtime(benches, out_dir):
    names = [b["benchmark"] for b in benches]
    x = np.arange(len(names))
    width = 0.8 / len(BACKENDS)
    fig, ax = plt.subplots(figsize=(max(7, 1.5 * len(names)), 4))
    for i, bk in enumerate(BACKENDS):
        vals = []
        for b in benches:
            be = b["backends"]
            base = be.get("cpython", {}).get("runtime_s")
            v = be.get(bk, {}).get("runtime_s")
            vals.append((v / base) if (v and base) else np.nan)
        ax.bar(x + i * width, vals, width, label=LABELS[bk].replace("\n", " "),
               color=COLORS[bk])
    ax.set_yscale("log")
    ax.axhline(1.0, color="#444", lw=0.8, ls="--")
    ax.set_xticks(x + width * (len(BACKENDS) - 1) / 2)
    ax.set_xticklabels(names, rotation=20, ha="right", fontsize=9)
    ax.set_ylabel("runtime relative to CPython\n(lower = faster, log)", fontsize=9)
    ax.set_title("Runtime by backend, normalised to CPython = 1", fontsize=11)
    ax.legend(fontsize=8, ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.18))
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    _save(fig, out_dir, "summary_runtime")


def summary_memory(benches, out_dir):
    names = [b["benchmark"] for b in benches]
    x = np.arange(len(names))
    width = 0.8 / len(BACKENDS)
    fig, ax = plt.subplots(figsize=(max(7, 1.5 * len(names)), 4))
    for i, bk in enumerate(BACKENDS):
        vals = [b["backends"].get(bk, {}).get("rss_kb", np.nan) / 1024.0
                for b in benches]
        ax.bar(x + i * width, vals, width, label=LABELS[bk].replace("\n", " "),
               color=COLORS[bk])
    ax.set_yscale("log")
    ax.set_xticks(x + width * (len(BACKENDS) - 1) / 2)
    ax.set_xticklabels(names, rotation=20, ha="right", fontsize=9)
    ax.set_ylabel("peak RSS (MB, log)", fontsize=9)
    ax.set_title("Peak memory by backend", fontsize=11)
    ax.legend(fontsize=8, ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.18))
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    _save(fig, out_dir, "summary_memory")


def _fmt(v, nd=3):
    if v is None:
        return "--"
    if v >= 100:
        return "%.0f" % v
    return ("%." + str(nd) + "g") % v


def write_report_body(benches, out_dir):
    """Emit report_body.tex: data tables + figure includes, all driven by the
    JSON so the report always matches the run. tools/benchmarks.tex \\input's it.
    """
    L = []
    a = L.append
    a("% Auto-generated by plot_rpython.py -- do not edit by hand.")
    # --- runtime + compile-time table ---
    a(r"\begin{table}[h]\centering\small")
    a(r"\caption{Runtime (best of 5 wall-clock seconds) and C-compiler time. "
      r"CPython and PyPy3 have no compile step.}")
    a(r"\begin{tabular}{lrrrr@{\hskip 2em}rr}\toprule")
    a(r"& \multicolumn{4}{c}{runtime (s)} & \multicolumn{2}{c}{compile (s)}\\")
    a(r"\cmidrule(lr){2-5}\cmidrule(lr){6-7}")
    a(r"benchmark & CPython & PyPy3 & py2c+gcc & self-hosted "
      r"& gcc & self-hosted\\\midrule")
    for b in benches:
        be = b["backends"]
        def rt(k): return _fmt(be.get(k, {}).get("runtime_s"))
        a("%s & %s & %s & %s & %s & %s & %s\\\\" % (
            b["benchmark"].replace("_", r"\_"),
            rt("cpython"), rt("pypy3"), rt("gcc"), rt("selfhost"),
            _fmt(be.get("gcc", {}).get("compile_s")),
            _fmt(be.get("selfhost", {}).get("compile_s"))))
    a(r"\bottomrule\end{tabular}\end{table}")
    # --- memory table ---
    a(r"\begin{table}[h]\centering\small")
    a(r"\caption{Peak resident set size (MB).}")
    a(r"\begin{tabular}{lrrrr}\toprule")
    a(r"benchmark & CPython & PyPy3 & py2c+gcc & self-hosted\\\midrule")
    for b in benches:
        be = b["backends"]
        def mb(k):
            v = be.get(k, {}).get("rss_kb")
            return _fmt(v / 1024.0) if v else "--"
        a("%s & %s & %s & %s & %s\\\\" % (
            b["benchmark"].replace("_", r"\_"),
            mb("cpython"), mb("pypy3"), mb("gcc"), mb("selfhost")))
    a(r"\bottomrule\end{tabular}\end{table}")
    # --- summary figures ---
    for stem, cap in [
        ("summary_runtime", "Runtime of every benchmark and backend, "
         "normalised to CPython (log scale; below the dashed line is faster "
         "than CPython)."),
        ("summary_memory", "Peak resident memory of every benchmark and "
         "backend (log scale).")]:
        a(r"\begin{figure}[h]\centering")
        a(r"\includegraphics[width=\linewidth]{%s.pdf}" % stem)
        a(r"\caption{%s}\end{figure}" % cap)
    a(r"\clearpage")
    # --- per-benchmark figures ---
    a(r"\section{Per-benchmark detail}")
    for b in benches:
        name = b["benchmark"]
        a(r"\begin{figure}[h]\centering")
        a(r"\includegraphics[width=\linewidth]{%s.pdf}" % name)
        a(r"\caption{\texttt{%s}: runtime, peak memory, and compile time "
          r"across the four backends.}" % name.replace("_", r"\_"))
        a(r"\end{figure}")
    body = os.path.join(out_dir, "report_body.tex")
    with open(body, "w") as f:
        f.write("\n".join(L) + "\n")
    return body


def main():
    out_dir = (sys.argv[1] if len(sys.argv) > 1
               else os.environ.get("BENCH_PLOT_DIR", "/tmp/shivyc_benchmarks"))
    os.makedirs(out_dir, exist_ok=True)
    with open(RESULTS) as f:
        benches = json.load(f)
    for b in benches:
        per_benchmark(b, out_dir)
    summary_runtime(benches, out_dir)
    summary_memory(benches, out_dir)
    write_report_body(benches, out_dir)
    stems = [b["benchmark"] for b in benches] + ["summary_runtime",
                                                 "summary_memory"]
    print("Wrote %d figures (PNG+PDF) to %s:" % (len(stems), out_dir))
    for s in stems:
        print("  %s.png / %s.pdf" % (s, s))


if __name__ == "__main__":
    main()
