#!/usr/bin/env python3
"""Render results/results.json to a 2x2 PNG: four features vs gcc -O0."""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "results", "results.json")) as f:
    data = {b["benchmark"]: b for b in json.load(f)}

C_BASE = "#4C72B0"   # ShivyCX feature OFF (baseline)
C_FEAT = "#DD8452"   # ShivyCX feature ON
C_GCC = "#999999"    # gcc -O0

TITLES = {
    "nbit_globals": "_Nbit globals  (xmm15 bit-packing)",
    "contracts_simd": "Contracts -> fallback-free SSE2",
    "stackless": "-fstackless-calls  (tail-call + FPO)",
    "metamorphic": "-fmetamorphic  (self-modifying return)",
}
ORDER = ["nbit_globals", "contracts_simd", "stackless", "metamorphic"]

fig, axes = plt.subplots(2, 2, figsize=(13, 9))
fig.suptitle("ShivyCX extended-C features vs gcc -O0  (feature ON vs OFF, same compiler)",
             fontsize=14, fontweight="bold")

for ax, key in zip(axes.flat, ORDER):
    cfgs = data[key]["configs"]
    labels, times, colors = [], [], []
    for c in cfgs:
        name = c["name"]
        if name.startswith("gcc"):
            colors.append(C_GCC); short = "gcc -O0"
        elif c["baseline"]:
            colors.append(C_BASE); short = "ShivyCX\n(feature off)"
        else:
            colors.append(C_FEAT); short = "ShivyCX\n(feature on)"
        labels.append(short)
        times.append(c["time_s"])

    bars = ax.bar(range(len(cfgs)), times, color=colors)
    ax.set_xticks(range(len(cfgs)))
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_title(TITLES[key], fontsize=12, fontweight="bold")
    ax.set_ylabel("seconds (best of 5)")
    top = max(times)
    ax.set_ylim(0, top * 1.25)
    base = next(c["time_s"] for c in cfgs if c["baseline"])
    for i, (c, b) in enumerate(zip(cfgs, bars)):
        v = c["time_s"]
        tag = "%.3fs" % v
        if not c["baseline"]:
            tag += "  %.2fx" % (base / v)
        ax.text(i, v + top * 0.02, tag, ha="center", va="bottom", fontsize=8)

    # one static-codegen witness caption per panel (off -> on)
    off = next(c for c in cfgs if c["baseline"])
    on = next(c for c in cfgs if not c["baseline"] and not c["name"].startswith("gcc"))
    cap = "codegen witness:  off = %s   ->   on = %s" % (off["metric"], on["metric"])
    ax.text(0.5, -0.22, cap, transform=ax.transAxes, ha="center",
            va="top", fontsize=8, style="italic", color="#555")

plt.tight_layout(rect=[0, 0.05, 1, 0.95])
plt.subplots_adjust(hspace=0.55)
out = os.path.join(HERE, "results", "benchmarks.png")
plt.savefig(out, dpi=130)
print("wrote", out)


# ---------------------------------------------------------------------------
# Second figure: capability / static-metric benchmarks (no like-for-like time)
# ---------------------------------------------------------------------------
me = next((b for b in data.values() if b["benchmark"] == "member_elim"), None)
th = next((b for b in data.values() if b["benchmark"] == "threads_leftright"), None)
ms = next((b for b in data.values() if b["benchmark"] == "memory_safety"), None)

if me and th and ms:
    fig2, (a0, a1, a2) = plt.subplots(1, 3, figsize=(15, 5))
    fig2.suptitle("ShivyCX whole-program capabilities gcc structurally lacks",
                  fontsize=14, fontweight="bold")

    # Panel A: member elimination -> .bss bytes
    labels = ["ShivyCX\n+elim", "ShivyCX\nbaseline", "gcc -O0"]
    vals = [me["on_bytes"], me["off_bytes"], me["gcc_bytes"]]
    a0.bar(labels, vals, color=[C_FEAT, C_BASE, C_GCC])
    for i, v in enumerate(vals):
        a0.text(i, v, " %d B" % v, ha="center", va="bottom", fontsize=9)
    a0.set_title("Unused-member elimination\n`table[1000]` .bss size", fontweight="bold")
    a0.set_ylabel("bytes")
    a0.set_ylim(0, max(vals) * 1.18)
    a0.text(0.5, -0.16, "%s  (%.0fx smaller; gcc layout is ABI-fixed)"
            % (me["removed"], me["off_bytes"] / me["on_bytes"]),
            transform=a0.transAxes, ha="center", va="top", fontsize=8,
            style="italic", color="#555")

    # Panel B: left/right threads -> registers saved per context switch
    labels = ["left/right\npartition", "save-all\n(naive)"]
    vals = [th["regs_saved"], th["regs_save_all"]]
    a1.bar(labels, vals, color=[C_FEAT, C_GCC])
    for i, v in enumerate(vals):
        a1.text(i, v, " %d regs" % v, ha="center", va="bottom", fontsize=10)
    a1.set_title("Register-partitioned threads\nregisters saved per context switch",
                 fontweight="bold")
    a1.set_ylabel("registers saved/restored")
    a1.set_ylim(0, max(vals) * 1.18)
    a1.text(0.5, -0.16, "%.1fx less switch state; gcc has no per-thread bank concept"
            % (th["regs_save_all"] / th["regs_saved"]),
            transform=a1.transAxes, ha="center", va="top", fontsize=8,
            style="italic", color="#555")

    # Panel C: memory-safety detection matrix
    rows = ms["rows"]
    a2.set_title("Whole-program memory safety\nbug detected? (green=yes)", fontweight="bold")
    a2.set_xlim(0, 2)
    a2.set_ylim(0, len(rows))
    a2.set_xticks([0.5, 1.5])
    a2.set_xticklabels(["ShivyCX", "gcc -O0"])
    a2.set_yticks([i + 0.5 for i in range(len(rows))])
    a2.set_yticklabels(["%s\n(%s)" % (r["case"], r["bug"]) for r in reversed(rows)],
                       fontsize=8)
    for j, r in enumerate(reversed(rows)):
        for k, det in enumerate([r["shivyc_detects"], r["gcc_detects"]]):
            a2.add_patch(plt.Rectangle((k, j), 1, 1,
                                       facecolor="#55A868" if det else "#C44E52",
                                       edgecolor="white"))
            a2.text(k + 0.5, j + 0.5, "YES" if det else "no", ha="center", va="center",
                    color="white", fontweight="bold", fontsize=9)
    a2.text(0.5, -0.16, "ShivyCX catches the cross-TU UAF gcc misses; auto-free is unique",
            transform=a2.transAxes, ha="center", va="top", fontsize=8,
            style="italic", color="#555")

    plt.tight_layout(rect=[0, 0.04, 1, 0.93])
    out2 = os.path.join(HERE, "results", "benchmarks2.png")
    plt.savefig(out2, dpi=130)
    print("wrote", out2)
