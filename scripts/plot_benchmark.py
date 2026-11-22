"""Draws docs/benchmark_chart.png from a benchmark JSON (default: docs/benchmark_results.json).

    python scripts/plot_benchmark.py [results/benchmark_xxx.json]

Left: four key defences per jammer type. Right: every defence's average delivery, and the extra transmit power
it spends to get there (the cost that 'just shout louder' hides). The same numbers are tabled in docs/BENCHMARK.md.
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "benchmark_results.json"
d = json.loads(src.read_text())
LABEL = {"none": "No defence", "fixed_hopping": "Fixed hopping", "energy": "Energy detector", "fhss_keyed": "Secret fast hopping",
         "max_power": "Brute force (max power)", "afh_power": "Rules, no CNN", "rl": "RL agent", "aegis": "ABHEDYA AI",
         "hybrid": "ABHEDYA + RL"}
KEY = [x for x in ["fixed_hopping", "max_power", "afh_power", "aegis"] if any(r["defender"] == x for r in d["rows"])]
# fixed categorical order, validated (light): lightness band, chroma, CVD and normal-vision separation all pass
COL = dict(zip(["fixed_hopping", "max_power", "afh_power", "aegis"], ["#2a78d6", "#eb6834", "#1baf7a", "#6250d6"]))
INK, MUTED, GRID, BG, NEUTRAL = "#1f1f1d", "#6b6a63", "#e6e5e0", "#fcfcfb", "#b9b8b1"
jams = list(dict.fromkeys(r["jammer"] for r in d["rows"]))
val = {(r["jammer"], r["defender"]): r["delivery_pct"] for r in d["rows"]}
ov = {o["defender"]: o for o in d["overall"]}
order = sorted(ov, key=lambda k: ov[k]["delivery_pct"])

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": GRID, "text.color": INK,
                     "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED})
fig = plt.figure(figsize=(15, 6.0), facecolor=BG)
a1 = fig.add_axes([0.05, 0.22, 0.47, 0.64])
a2 = fig.add_axes([0.66, 0.22, 0.15, 0.64])
a3 = fig.add_axes([0.84, 0.22, 0.13, 0.64])

n, w = len(KEY), 0.8 / len(KEY)
for k, dn in enumerate(KEY):
    xs = [i - 0.4 + w * (k + 0.5) for i in range(len(jams))]
    a1.bar(xs, [val.get((j, dn), 0) for j in jams], width=w - 0.03, color=COL[dn], label=LABEL[dn], edgecolor=BG, linewidth=1)
a1.set_xticks(range(len(jams)), jams, fontsize=9.5)
a1.set_xlim(-0.5, len(jams) - 0.5)
a1.set_ylim(0, 100)
a1.set_ylabel("messages delivered under attack (%)")
a1.set_title("Each jammer type: four key defences", loc="left", fontsize=12, color=INK)
a1.legend(ncol=4, frameon=False, loc="upper left", bbox_to_anchor=(0, -0.08), fontsize=9.5)

ys = list(range(len(order)))
a2.barh(ys, [ov[x]["delivery_pct"] for x in order], height=0.64, edgecolor=BG,
        color=[COL["aegis"] if x == "aegis" else NEUTRAL for x in order])
for y, x in zip(ys, order):
    lo, hi = ov[x].get("delivery_ci95") or [None, None]
    a2.text(ov[x]["delivery_pct"] + 2, y, f"{ov[x]['delivery_pct']:.1f} %", va="center", fontsize=9.5, color=INK,
            fontweight="bold" if x == "aegis" else "normal")
a2.set_yticks(ys, [LABEL[x] for x in order])
a2.set_xlim(0, 125)
a2.set_xticks([0, 50, 100])
a2.set_title("Average delivered", loc="left", fontsize=12, color=INK)

a3.barh(ys, [ov[x].get("mean_boost_db", 0.0) for x in order], height=0.64, edgecolor=BG,
        color=[COL["aegis"] if x == "aegis" else NEUTRAL for x in order])
for y, x in zip(ys, order):
    a3.text(ov[x].get("mean_boost_db", 0.0) + 0.25, y, f"{ov[x].get('mean_boost_db', 0.0):.1f} dB", va="center", fontsize=9.5,
            color=INK, fontweight="bold" if x == "aegis" else "normal")
a3.set_yticks(ys, [""] * len(ys))
a3.set_xlim(0, 12.5)
a3.set_xticks([0, 3, 6, 9])
a3.set_title("Extra power used", loc="left", fontsize=12, color=INK)

for a in (a1, a2, a3):
    a.set_facecolor(BG)
    a.grid(axis="y" if a is a1 else "x", color=GRID, lw=0.8)
    a.set_axisbelow(True)
    for s in ("top", "right"):
        a.spines[s].set_visible(False)
fig.suptitle(f"ABHEDYA benchmark (simulated) · {d['seeds']} seeds × {d['duration_s']:.0f} s per run · every defence faces the "
             "identical seeded attack", x=0.01, y=0.97, ha="left", fontsize=11, color=MUTED)
out = ROOT / "docs" / "benchmark_chart.png"
fig.savefig(out, dpi=140)
print(out)
