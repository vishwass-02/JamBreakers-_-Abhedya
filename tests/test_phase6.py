"""Phase 6: the smart (learning) jammer, the RL hopping agent, and the full benchmark."""
from __future__ import annotations

import collections
import json
from pathlib import Path

import numpy as np
from jsonschema import Draft202012Validator

from ai.rl import ACTIONS, N_STATES, RLDefender, load_q
from backend.commands import parse_command
from backend.config import Settings
from backend.protocol import load_schema
from sim.experiment import run_episode
from sim.world import SimArena

ROOT = Path(__file__).resolve().parent.parent


def _smart(defender: str, seconds: float = 16.0):
    r = run_episode("smart", defender, seed=3, duration_s=seconds, jam_at_s=1.0, pos=(300.0, 150.0))
    return r, next(j for j in r.world.jammers if j.type == "smart")


def test_smart_jammer_learns_a_different_tactic_per_defence():
    _, vs_fixed = _smart("fixed_hopping")
    _, vs_energy = _smart("energy")
    best = lambda jm: jm.TACTICS[int(np.argmax(jm.value))]  # noqa: E731
    assert best(vs_fixed) == "predict"            # learned fixed hopping's public pattern
    assert best(vs_energy) in ("follow", "predict")
    picks = collections.Counter(h[1] for h in vs_fixed.history)
    assert picks.most_common(1)[0][0] == "predict"  # and exploited it most of the time


def test_smart_jammer_finds_nothing_that_works_against_aegis():
    r, jm = _smart("aegis")
    assert jm.value.max() < 0.35                  # no tactic lands on our channel often
    assert r.delivery_pct > 80
    rf, _ = _smart("fixed_hopping")
    assert r.delivery_pct > rf.delivery_pct + 30


def test_smart_jammer_only_uses_heard_channels_and_reports_its_tactic():
    a = SimArena(Settings())
    out = a.handle(parse_command({"type": "cmd", "action": "launch_jammer", "jammer_type": "smart", "pos": [300, 150], "player": "Judge"}))
    assert not any(m["type"] == "error" for m in out)
    v = Draft202012Validator(load_schema())
    seen = None
    for _ in range(60):
        for m in a.step(0.05):
            if m["type"] == "state":
                v.validate(m)
                sm = [j for j in m["jammers"] if j["type"] == "smart"]
                if sm:
                    seen = sm[0]
    assert seen and seen["tactic"] in ("follow", "predict", "wide", "pulse", "spoof")
    assert set(seen["tactic_values"]) == {"follow", "predict", "wide", "pulse", "spoof"}
    jm = next(j for j in a.worlds["main"].jammers if j.type == "smart")
    assert set(jm.targets) <= set(np.flatnonzero(jm.heard > 0).tolist())


def test_rl_agent_is_trained_and_beats_fixed_hopping():
    q = load_q()
    assert q.shape == (N_STATES, len(ACTIONS))
    meta = json.loads((ROOT / "ai" / "weights" / "rl_q.json").read_text())
    assert meta["visited_states"] > 40
    rl = run_episode("follower", "rl", seed=4, duration_s=10, jam_at_s=1.0, pos=(150, 80))
    fx = run_episode("follower", "fixed_hopping", seed=4, duration_s=10, jam_at_s=1.0, pos=(150, 80))
    assert isinstance(rl.world.defender, RLDefender)
    assert rl.delivery_pct > 85 and rl.delivery_pct > fx.delivery_pct + 50


def test_published_benchmark_has_aegis_on_top():
    d = json.loads((ROOT / "docs" / "benchmark_results.json").read_text())
    jams = {r["jammer"] for r in d["rows"]}
    defs = {r["defender"] for r in d["rows"]}
    assert {"smart", "follower", "barrage", "novel"} <= jams
    assert defs == {"none", "fixed_hopping", "energy", "fhss_keyed", "max_power", "afh_power", "rl", "aegis", "hybrid"}
    mean = {o["defender"]: o["delivery_pct"] for o in d["overall"]}
    assert max(mean, key=mean.get) in ("aegis", "hybrid")   # done-when of build-plan step 10: the CNN defender on top
    assert mean["hybrid"] >= mean["aegis"] >= mean["rl"] > mean["fixed_hopping"]
    # the claims in docs/EVIDENCE.md: the AI beats the same toolbox driven by rules, significantly, and with less power
    paired = {p["vs"]: p for p in d["paired"]}
    assert paired["afh_power"]["ci95_points"][0] > 0 and paired["fixed_hopping"]["ai_losses"] == 0
    boost = {o["defender"]: o["mean_boost_db"] for o in d["overall"]}
    assert boost["aegis"] < boost["afh_power"] / 2 and boost["aegis"] < boost["max_power"]
    assert (ROOT / "docs" / "benchmark_chart.png").exists()


def test_aegis_handles_a_follower_hiding_under_a_sweep():
    """Several judges launch jammers at once: a follower under a sweep is invisible per channel; the probe finds it."""
    from sim.jammers import make_jammer
    from sim.phy import NumpyPhy
    from sim.world import SimWorld

    def run(defender):
        w = SimWorld("main", 5, defender, NumpyPhy())
        w.run_slots(100)
        s0, d0 = w.total_sent, w.total_delivered
        w.launch(make_jammer("J1", "follower", (200, 100), None, None))
        w.launch(make_jammer("J2", "sweep", (500, 300), None, None))
        w.run_slots(1200)
        return 100 * (w.total_delivered - d0) / max(w.total_sent - s0, 1), w
    aegis, w = run("aegis")
    energy, _ = run("energy")
    assert w.hop_mode == "keyed"                   # it found the hidden follower and kept keyed hopping
    assert aegis > 50 and aegis > energy + 40


def test_jammer_cap_switches_off_the_oldest():
    from sim.world import MAX_JAMMERS
    a = SimArena(Settings())
    for i in range(MAX_JAMMERS + 3):
        out = a.handle(parse_command({"type": "cmd", "action": "launch_jammer", "jammer_type": "random", "pos": [100 + 10 * i, 80]}))
    assert any("At most" in m.get("text", "") for m in out)
    for _ in range(4):
        a.step(0.05)
    ids = [j.id for j in a.worlds["main"].jammers]
    assert len(ids) == MAX_JAMMERS and "J1" not in ids and f"J{MAX_JAMMERS + 3}" in ids
