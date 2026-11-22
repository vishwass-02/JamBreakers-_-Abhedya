"""Does convoy speed change the result? Same benchmark episodes, run twice: trucks at the benchmark's speeds
(9 and 7 m/s, 32 / 25 km/h) and at the live arena's demo speeds (20 and 16 m/s, 72 / 58 km/h).

    python scripts/truck_speed_check.py          -> docs/truck_speed_results.json and a table

Faster trucks move through the jammer's footprint and change link quality faster, so the CNN sees less
consistent windows. This measures by how much, instead of guessing.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sim.network as N  # noqa: E402
import sim.world as W  # noqa: E402
from sim.benchmark import JAMMERS, TRUE_CLASS, jammer_position  # noqa: E402
from sim.experiment import run_episode  # noqa: E402

SPEEDS = {"benchmark (32 / 25 km/h)": N.BENCH_TRUCK_SPEEDS, "demo arena (72 / 58 km/h)": N.LIVE_TRUCK_SPEEDS}


def run(seeds: int = 5, duration_s: float = 20.0, defender: str = "aegis") -> dict:
    out = {"defender": defender, "seeds": seeds, "duration_s": duration_s, "speeds": {}, "created": time.strftime("%Y-%m-%d %H:%M")}
    orig = W.arena_nodes
    try:
        for label, sp in SPEEDS.items():
            W.arena_nodes = lambda sp=sp: orig(sp)
            per = {}
            for j in JAMMERS:
                dv, votes, right = [], 0, 0
                for k in range(seeds):
                    from ai.defender import SIGNATURES
                    SIGNATURES.clear()
                    r = run_episode(j, defender, seed=7 + k, duration_s=duration_s, pos=jammer_position(7 + k, j))
                    dv.append(r.delivery_pct)
                    if TRUE_CLASS[j]:
                        v = list(getattr(r.world.defender, "vote_log", []))
                        votes += len(v)
                        right += sum(x == TRUE_CLASS[j] for x in v)
                per[j] = {"delivery_pct": round(float(np.mean(dv)), 1), "votes": votes, "correct": right,
                          "accuracy_pct": round(100 * right / votes, 1) if votes else None}
            acc_v = [p["accuracy_pct"] for p in per.values() if p["accuracy_pct"] is not None]
            out["speeds"][label] = {"per_jammer": per, "delivery_pct": round(float(np.mean([p["delivery_pct"] for p in per.values()])), 1),
                                    "accuracy_pct": round(float(np.mean(acc_v)), 1),       # each jammer type counts equally
                                    "accuracy_pooled_pct": round(100 * sum(p["correct"] for p in per.values())
                                                                 / max(1, sum(p["votes"] for p in per.values())), 1)}  # as in the benchmark
    finally:
        W.arena_nodes = orig
    return out


def main() -> None:
    r = run()
    (ROOT / "docs" / "truck_speed_results.json").write_text(json.dumps(r, indent=2))
    for label, s in r["speeds"].items():
        print(f"{label:28s} delivered {s['delivery_pct']:5.1f} %   accuracy pooled {s['accuracy_pooled_pct']:5.1f} %"
              f"  per-type mean {s['accuracy_pct']:5.1f} %")
        print("   " + "  ".join(f"{j} {p['accuracy_pct']}" for j, p in s["per_jammer"].items() if p["accuracy_pct"] is not None))


if __name__ == "__main__":
    main()
