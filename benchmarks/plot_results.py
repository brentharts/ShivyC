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
