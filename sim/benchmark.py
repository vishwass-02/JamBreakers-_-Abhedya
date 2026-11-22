"""Seed-replayed benchmark: the same jamming run against every defender.

For each jammer type and each seed, every defender faces an identical run: same seed (same motion,
fading, jammer randomness), same jammer, same position, same start time. Only the defence differs.

Reported per jammer x defender (mean over seeds) and overall per defender:
  delivery %      packets that reached the command post after the jammer started
  outage s        time the network was delivering less than half its packets
  hop latency     first jammed slot -> whole network on the new channel (median and p95, pooled)
  accuracy %      AEGIS only: raw CNN verdicts that match the jammer actually on the air
                  (a follower is labelled 'random': in a single window it is a narrowband jammer)

    python -m sim.benchmark                 # 3 seeds, all defenders, prints a table, saves results/
    python -m sim.benchmark --quick         # 1 seed, fixed hopping vs AEGIS
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

import numpy as np

JAMMERS = ["barrage", "random", "sweep", "reactive", "deceptive", "accidental", "follower", "smart", "novel"]
DEFENDERS = ["none", "fixed_hopping", "energy", "fhss_keyed", "max_power", "afh_power", "rl", "aegis", "hybrid"]
LABELS = {"none": "No defence", "fixed_hopping": "Fixed hopping (FHSS)", "energy": "Energy detector",
          "fhss_keyed": "Secret fast hopping", "max_power": "Brute force (max power + BPSK)",
          "afh_power": "Rule-based adaptive (no CNN)", "rl": "RL agent (Q-learning)", "aegis": "ABHEDYA AI",
          "hybrid": "Hybrid (ABHEDYA + RL)"}
TRUE_CLASS = {"barrage": "barrage", "random": "random", "sweep": "sweep", "reactive": "reactive",
              "deceptive": "deceptive", "accidental": "accidental", "follower": "random",
              "smart": None, "novel": None}            # changes tactic every second: no single right label, so not scored
RESULTS = Path(__file__).resolve().parent.parent / "results"


def jammer_position(seed: int, jammer: str) -> tuple[float, float]:
    """Deterministic, different per seed and jammer, always somewhere that bites the network."""
    rng = np.random.default_rng(seed * 1000 + JAMMERS.index(jammer))
    return float(rng.uniform(-100, 700)), float(rng.uniform(-200, 500))


def run_one(job: tuple) -> dict:
    """One episode. Top-level so it can run in a worker process (Windows spawns)."""
    jammer, defender, seed, duration_s, jam_at_s = job
    from .experiment import run_episode
    try:
        from ai.defender import SIGNATURES
        SIGNATURES.clear()                      # every run starts with an empty signature memory (fair comparison)
    except ImportError:
        pass
    r = run_episode(jammer, defender, seed=seed, duration_s=duration_s, jam_at_s=jam_at_s, pos=jammer_position(seed, jammer))
    votes = list(getattr(r.world.defender, "vote_log", []))
    if TRUE_CLASS[jammer] is None:
        votes = []
    correct = sum(v == TRUE_CLASS[jammer] for v in votes)
    return {"jammer": jammer, "defender": defender, "seed": seed, "delivery_pct": r.delivery_pct,
            "outage_ms": r.outage_ms, "latencies": list(r.world.latencies), "hops": r.hops,
            "votes": len(votes), "correct": correct,
            "forged": r.world.frames_forged, "forged_accepted": r.world.frames_forged_accepted,
            "boost_db": r.mean_boost_db, "slow_rate_pct": r.slow_rate_pct}


def _t95(n: int) -> float:
    """Two-sided 95 % t quantile for n samples (n - 1 degrees of freedom)."""
    t = {2: 12.71, 3: 4.30, 4: 3.18, 5: 2.78, 6: 2.57, 7: 2.45, 8: 2.36, 9: 2.31, 10: 2.26, 12: 2.20, 15: 2.14, 20: 2.09}
    return t.get(n, 2.0 if n > 20 else 2.3)


def _ci(v) -> tuple[float, list]:
    v = np.asarray(v, float)
    if len(v) < 2:
        return 0.0, [round(float(v.mean()), 1)] * 2
    sd = float(v.std(ddof=1))
    half = _t95(len(v)) * sd / np.sqrt(len(v))
    return round(sd, 1), [round(float(v.mean() - half), 1), round(float(min(100.0, v.mean() + half)), 1)]


def aggregate(raw: list[dict], jammers: list[str], defenders: list[str]) -> tuple[list[dict], list[dict]]:
    """Per (jammer, defence): mean over seeds, standard deviation, 95 % CI of the mean, worst run,
    hop latency p50/p95/p99. Overall: the mean over jammers, its spread over seeds and the worst run."""
    rows, overall = [], []
    for j in jammers:
        for d in defenders:
            rs = [r for r in raw if r["jammer"] == j and r["defender"] == d]
            if not rs:
                continue
            lat = [x for r in rs for x in r["latencies"]]
            votes = sum(r["votes"] for r in rs)
            dv = [r["delivery_pct"] for r in rs]
            sd, ci = _ci(dv)
            rows.append({
                "jammer": j, "defender": d, "runs": len(rs),
                "delivery_pct": round(float(np.mean(dv)), 1),
                "delivery_min_pct": round(float(np.min(dv)), 1),
                "delivery_sd": sd, "delivery_ci95": ci,
                "outage_ms": round(float(np.mean([r["outage_ms"] for r in rs])), 0),
                "hop_latency_median_ms": round(float(np.median(lat)), 1) if lat else None,
                "hop_latency_p95_ms": round(float(np.percentile(lat, 95)), 1) if lat else None,
                "hop_latency_p99_ms": round(float(np.percentile(lat, 99)), 1) if lat else None,
                "accuracy_pct": round(100 * sum(r["correct"] for r in rs) / votes, 1) if votes else None,
                "forged_accepted": int(sum(r["forged_accepted"] for r in rs)),
                "mean_boost_db": round(float(np.mean([r.get("boost_db", 0.0) for r in rs])), 2),
                "slow_rate_pct": round(float(np.mean([r.get("slow_rate_pct", 0.0) for r in rs])), 1),
            })
    for d in defenders:
        rs = [r for r in rows if r["defender"] == d]
        lat = [x for r in raw if r["defender"] == d for x in r["latencies"]]
        votes = sum(r["votes"] for r in raw if r["defender"] == d)
        per_seed: dict = {}
        for r in raw:
            if r["defender"] == d:
                per_seed.setdefault(r["seed"], []).append(r["delivery_pct"])
        sd, ci = _ci([np.mean(v) for v in per_seed.values()])
        overall.append({
            "defender": d, "label": LABELS[d],
            "delivery_pct": round(float(np.mean([r["delivery_pct"] for r in rs])), 1),
            "delivery_sd_over_seeds": sd, "delivery_ci95": ci,
            "worst_jammer_pct": round(float(min(r["delivery_pct"] for r in rs)), 1),
            "worst_run_pct": round(float(min(r["delivery_pct"] for r in raw if r["defender"] == d)), 1),
            "outage_ms": round(float(np.mean([r["outage_ms"] for r in rs])), 0),
            "mean_boost_db": round(float(np.mean([r["mean_boost_db"] for r in rs])), 2),
            "slow_rate_pct": round(float(np.mean([r["slow_rate_pct"] for r in rs])), 1),
            "hop_latency_median_ms": round(float(np.median(lat)), 1) if lat else None,
            "hop_latency_p95_ms": round(float(np.percentile(lat, 95)), 1) if lat else None,
            "hop_latency_p99_ms": round(float(np.percentile(lat, 99)), 1) if lat else None,
            "accuracy_pct": round(100 * sum(r["correct"] for r in raw if r["defender"] == d) / votes, 1) if votes else None,
            "wins_vs_fixed": sum(1 for j in jammers if _get(rows, j, d) and _get(rows, j, "fixed_hopping")
                                 and _get(rows, j, d)["delivery_pct"] > _get(rows, j, "fixed_hopping")["delivery_pct"]),
        })
    return rows, overall


def paired_vs(runs: list[dict], ai: str = "aegis") -> list[dict]:
    """Paired comparison: the same seed and jammer (identical attack) run with the AI and with each other defence.
    Mean difference in delivery, its 95 % CI, and how many of the paired runs the AI won, tied or lost."""
    by = {(r["jammer"], r["seed"], r["defender"]): r for r in runs}
    out = []
    for d in sorted({r["defender"] for r in runs} - {ai}):
        diffs = [by[(j, s, ai)]["delivery_pct"] - by[(j, s, d)]["delivery_pct"]
                 for (j, s, dd) in by if dd == d and (j, s, ai) in by]
        if len(diffs) < 2:
            continue
        sd, ci = _ci(diffs)
        out.append({"vs": d, "label": LABELS.get(d, d), "pairs": len(diffs), "mean_gain_points": round(float(np.mean(diffs)), 2),
                    "ci95_points": [round(ci[0], 2), round(ci[1], 2)] if ci else None, "sd": sd,
                    "ai_wins": sum(x > 0.05 for x in diffs), "ties": sum(abs(x) <= 0.05 for x in diffs),
                    "ai_losses": sum(x < -0.05 for x in diffs)})
    return out


def _get(rows, j, d):
    return next((r for r in rows if r["jammer"] == j and r["defender"] == d), None)


def save(result: dict, out_dir: Path = RESULTS) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    j_path, c_path = out_dir / f"benchmark_{stamp}.json", out_dir / f"benchmark_{stamp}.csv"
    result["files"] = [f"results/{j_path.name}", f"results/{c_path.name}"]
    j_path.write_text(json.dumps(result, indent=2))
    (out_dir / "benchmark_latest.json").write_text(json.dumps(result, indent=2))
    cols = ["jammer", "defender", "runs", "delivery_pct", "delivery_min_pct", "outage_ms", "hop_latency_median_ms",
            "hop_latency_p95_ms", "accuracy_pct", "forged_accepted"]
    for path in (c_path, out_dir / "benchmark_latest.csv"):
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in result["rows"]:
                w.writerow({k: r.get(k) for k in cols})
    return result["files"]


def run_benchmark(seed: int = 7, seeds: int = 3, duration_s: float = 12.0, jam_at_s: float = 2.0,
                  defenders: Optional[list[str]] = None, jammers: Optional[list[str]] = None, workers: Optional[int] = None,
                  progress: Optional[Callable[[float, str], None]] = None, out_dir: Optional[Path] = RESULTS) -> dict:
    defenders = defenders or DEFENDERS
    jammers = jammers or JAMMERS
    jobs = [(j, d, seed + k, duration_s, jam_at_s) for k in range(seeds) for j in jammers for d in defenders]
    workers = workers if workers is not None else max(1, min(4, (os.cpu_count() or 2) - 1))
    t0 = time.time()
    raw: list[dict] = []
    if workers <= 1:
        for i, job in enumerate(jobs):
            raw.append(run_one(job))
            if progress:
                progress((i + 1) / len(jobs), f"{job[0]} vs {LABELS[job[1]]}")
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(run_one, job): job for job in jobs}
            for i, fut in enumerate(as_completed(futs)):
                raw.append(fut.result())
                if progress:
                    job = futs[fut]
                    progress((i + 1) / len(jobs), f"{job[0]} vs {LABELS[job[1]]}")
    rows, overall = aggregate(raw, jammers, defenders)
    runs = sorted(({"jammer": r["jammer"], "defender": r["defender"], "seed": r["seed"], "delivery_pct": r["delivery_pct"],
                    "outage_ms": r["outage_ms"], "boost_db": r.get("boost_db", 0.0)} for r in raw),
                  key=lambda r: (r["jammer"], r["defender"], r["seed"]))
    result = {"seed": seed, "seeds": seeds, "duration_s": duration_s, "jam_at_s": jam_at_s, "jammers": jammers,
              "defenders": defenders, "rows": rows, "overall": overall, "runs": runs,
              "paired": paired_vs(runs, "aegis") if "aegis" in defenders else [], "elapsed_s": round(time.time() - t0, 1),
              "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    result["files"] = save(result, out_dir) if out_dir else []
    return result


def table(result: dict) -> str:
    ds = result["defenders"]
    lines = ["| Jammer | " + " | ".join(LABELS[d] for d in ds) + " |", "|---" * (len(ds) + 1) + "|"]
    for j in result["jammers"]:
        cells = [f"{_get(result['rows'], j, d)['delivery_pct']:.1f} %" for d in ds]
        lines.append(f"| {j} | " + " | ".join(cells) + " |")
    lines.append("| **mean** | " + " | ".join(f"**{o['delivery_pct']:.1f} %**" for o in result["overall"]) + " |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--duration", type=float, default=12.0)
    ap.add_argument("--quick", action="store_true", help="1 seed, fixed hopping vs AEGIS")
    ap.add_argument("--workers", type=int, default=None)
    a = ap.parse_args()
    kw = dict(seeds=1, defenders=["fixed_hopping", "aegis"]) if a.quick else dict(seeds=a.seeds)
    res = run_benchmark(seed=a.seed, duration_s=a.duration, workers=a.workers,
                        progress=lambda p, m: print(f"\r{p:5.0%} {m:40s}", end="", flush=True), **kw)
    print("\n\nPackets delivered under attack (mean over seeds):\n")
    print(table(res))
    print("\nOverall:")
    for o in res["overall"]:
        lat = f"{o['hop_latency_median_ms']} / {o['hop_latency_p95_ms']} ms" if o["hop_latency_median_ms"] is not None else "—"
        acc = f" · CNN accuracy {o['accuracy_pct']} %" if o["accuracy_pct"] is not None else ""
        print(f"  {o['label']:22s} delivery {o['delivery_pct']:5.1f} % · worst {o['worst_jammer_pct']:5.1f} % · "
              f"outage {o['outage_ms'] / 1000:4.1f} s · hop latency median/p95 {lat}{acc}")
    print(f"\nSaved: {', '.join(res['files'])}  ({res['elapsed_s']} s)")


if __name__ == "__main__":
    main()
