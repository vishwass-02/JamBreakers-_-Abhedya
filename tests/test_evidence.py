"""The stronger baselines, the paired statistics and the classifier stress test."""
from __future__ import annotations

import numpy as np

from ai.calibration import Calibrator
from sim.benchmark import LABELS, DEFENDERS, paired_vs
from sim.defenders import make_defender
from sim.experiment import run_episode
from sim.radio import POWER_CAP_DB


def test_every_defender_has_a_label_and_builds():
    for d in DEFENDERS:
        assert d in LABELS
        make_defender(d, np.random.default_rng(0))


def test_brute_force_runs_at_full_power_all_the_time():
    r = run_episode("barrage", "max_power", seed=7, duration_s=4.0, jam_at_s=1.0)
    assert r.mean_boost_db == POWER_CAP_DB and r.slow_rate_pct == 100.0


def test_rule_based_baseline_escalates_against_wideband_jamming():
    # a barrage lifts the whole band: avoiding channels cannot help, so the rules must raise power
    r = run_episode("barrage", "afh_power", seed=7, duration_s=8.0, jam_at_s=1.0)
    assert r.mean_boost_db > 3.0
    q = run_episode("barrage", "fixed_hopping", seed=7, duration_s=8.0, jam_at_s=1.0)
    assert r.delivery_pct > q.delivery_pct + 20


def test_secret_hopping_beats_a_follower_that_fixed_hopping_cannot():
    k = run_episode("follower", "fhss_keyed", seed=7, duration_s=8.0, jam_at_s=1.0)
    f = run_episode("follower", "fixed_hopping", seed=7, duration_s=8.0, jam_at_s=1.0)
    assert k.delivery_pct > f.delivery_pct + 20


def test_paired_comparison_counts_wins_and_losses():
    runs = [{"jammer": "a", "seed": s, "defender": d, "delivery_pct": v}
            for s, (x, y) in enumerate([(90, 80), (95, 96), (70, 70)]) for d, v in (("aegis", x), ("b", y))]
    p = paired_vs(runs)[0]
    assert p["vs"] == "b" and p["pairs"] == 3 and (p["ai_wins"], p["ties"], p["ai_losses"]) == (1, 1, 1)
    assert abs(p["mean_gain_points"] - 3.0) < 1e-9


def test_dropped_frames_are_filled_from_neighbours():
    w = np.arange(12 * 4, dtype=float).reshape(12, 4)
    f = Calibrator.fill_missing(w, [0, 5, 11])
    assert (f[0] == w[1]).all() and (f[5] == w[4]).all() and (f[11] == w[10]).all()
    assert (f[3] == w[3]).all()


def test_stress_test_calibration_removes_gain_error():
    from ai.stress_test import run
    r = run(n_per_class=6)
    c = r["conditions"]
    assert c["clean"]["calibrated"]["accuracy_pct"] >= 80
    # an unknown +6 dB gain error breaks the raw classifier; start-up calibration repairs it
    assert c["gain_+6db"]["calibrated"]["accuracy_pct"] > c["gain_+6db"]["raw"]["accuracy_pct"] + 30
