"""Headless runs: one jammer, one defender, a fixed seed. Used by tests and by the benchmark."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .jammers import make_jammer
from .phy import NumpyPhy
from .radio import SLOT_S
from .world import SimWorld

DEFAULT_JAMMER_POS = {"barrage": (220.0, 140.0), "random": (160.0, 90.0), "sweep": (200.0, 120.0),
                      "reactive": (150.0, 80.0), "deceptive": (150.0, 80.0), "accidental": (60.0, 30.0),
                      "follower": (150.0, 80.0), "smart": (150.0, 80.0), "novel": (180.0, 100.0),
                      "novel_staircase": (180.0, 100.0)}


@dataclass
class RunResult:
    jammer: str
    defender: str
    delivery_pct: float            # packets delivered after the jammer starts
    outage_ms: float
    hop_latency_median_ms: Optional[float]
    hop_latency_p95_ms: Optional[float]
    hops: int
    decisions: list
    world: SimWorld
    mean_boost_db: float = 0.0      # average extra transmit power while jammed (battery cost)
    slow_rate_pct: float = 0.0      # share of jammed time spent at the slower, more robust modulation


def run_episode(jammer: Optional[str], defender: str, seed: int = 7, duration_s: float = 15.0, jam_at_s: float = 2.0,
                pos: Optional[tuple] = None, power_dbm: Optional[float] = None, phy=None) -> RunResult:
    w = SimWorld("main", seed, defender, phy or NumpyPhy())
    n_total = int(duration_s / SLOT_S)
    n_pre = int(jam_at_s / SLOT_S)
    w.run_slots(n_pre)
    base_sent, base_deliv, base_out = w.total_sent, w.total_delivered, w.outage_ms
    if jammer:
        w.launch(make_jammer("J1", jammer, pos or DEFAULT_JAMMER_POS[jammer], power_dbm, None))
    decisions = []
    boost = 0.0
    bpsk = 0
    for _ in range(n_total - n_pre):
        boost += w.net.tx_boost_db
        bpsk += w.net.modulation != "QPSK"
        w.step_slot()
        decisions.extend(m for m in w.outbox if m["type"] == "decision")
        w.outbox.clear()
    sent, deliv = w.total_sent - base_sent, w.total_delivered - base_deliv
    lat = sorted(w.latencies)
    return RunResult(jammer or "none", w.defender_name, round(100 * deliv / max(sent, 1), 1), round(w.outage_ms - base_out, 1),
                     round(float(np.median(lat)), 1) if lat else None, round(float(np.percentile(lat, 95)), 1) if lat else None,
                     w.hops, decisions, w, round(boost / max(n_total - n_pre, 1), 2),
                     round(100 * bpsk / max(n_total - n_pre, 1), 1))
