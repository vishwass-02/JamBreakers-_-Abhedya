"""The numbers in the documents must match the result files they come from.

If a benchmark is re-run, these tests point at every document that now quotes an old figure.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def _j(p):
    return json.loads((ROOT / p).read_text(encoding="utf-8"))


def _t(p):
    return (ROOT / p).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def nums():
    b = _j("docs/benchmark_results.json")
    o = {x["defender"]: x for x in b["overall"]}
    paired = {p["vs"]: p for p in b["paired"]}
    rep = _j("ai/weights/report.json")
    stress = _j("docs/cnn_stress_results.json")
    truck = _j("docs/truck_speed_results.json")
    rob = _j("docs/robustness_results.json")
    first_speed = next(iter(truck["speeds"].values()))
    return {
        "ai": o["aegis"], "hybrid": o["hybrid"], "fixed": o["fixed_hopping"], "rules": o["afh_power"],
        "gain": paired["afh_power"], "held_out": round(100 * rep["accuracy"], 1), "infer_ms": rep["numpy_inference_ms_median"],
        "fresh": stress["conditions"]["clean"]["calibrated"]["accuracy_pct"], "per_type": first_speed["accuracy_pct"],
        "resp": rob["response_latency_ms"],
    }


def _pct(v):
    return f"{v:.1f} %"


def test_key_numbers_table_matches_the_result_files(nums):
    ev = _t("docs/EVIDENCE.md")
    key = ev[ev.index("## 0. Key numbers"):ev.index("## 1. Headline")]
    ai, hy, fx = nums["ai"], nums["hybrid"], nums["fixed"]
    for v, ci in ((ai["delivery_pct"], ai["delivery_ci95"]), (hy["delivery_pct"], hy["delivery_ci95"]), (fx["delivery_pct"], fx["delivery_ci95"])):
        assert f"**{_pct(v)}** (CI {ci[0]}–{ci[1]})" in key, (v, ci)
    g = nums["gain"]
    assert f"**+{g['mean_gain_points']:.1f} points** (paired CI +{g['ci95_points'][0]:.1f} to +{g['ci95_points'][1]:.1f})" in key
    assert f"**{_pct(nums['held_out'])}** | 1,680" in key
    assert f"**{_pct(ai['accuracy_pct'])}** | every CNN verdict" in key
    assert f"**{_pct(nums['per_type'])}**" in key
    assert f"**{_pct(nums['fresh'])}**" in key
    assert f"**p50 {ai['hop_latency_median_ms']:.0f} ms, p95 {ai['hop_latency_p95_ms']:.0f} ms**" in key
    assert f"**p50 {hy['hop_latency_median_ms']:.0f} ms, p95 {hy['hop_latency_p95_ms']:.0f} ms**" in key
    r = nums["resp"]
    assert f"**p50 {r['p50']:.0f} ms, p95 {r['p95']:.0f} ms, p99 {r['p99']:.0f} ms**" in key
    assert f"**{nums['infer_ms']:.1f} ms**" in key


def test_readme_headline_matches(nums):
    rd = _t("README.md")
    head = rd[:rd.index("## Quick start")]
    assert f"**{_pct(nums['ai']['delivery_pct'])}**" in head and f"**{_pct(nums['fixed']['delivery_pct'])}**" in head
    assert f"**+{nums['gain']['mean_gain_points']:.1f} points**" in head
    assert f"Accuracy (held-out test set, 1,680 windows captured live) | **{_pct(nums['held_out'])}**" in rd


def test_demo_script_quotes_current_figures(nums):
    ds = _t("docs/DEMO_SCRIPT.md")
    assert f"**{_pct(nums['hybrid']['delivery_pct'])}**" in ds        # AI ON in the demo is the hybrid
    assert f"{_pct(nums['ai']['delivery_pct'])}" in ds
    assert f"**{_pct(nums['fixed']['delivery_pct'])}**" in ds
    assert f"{nums['hybrid']['hop_latency_p95_ms']:.0f} ms p95" in ds
    assert f"{_pct(nums['held_out'])} on a held-out test set" in ds


STALE = ["54.3 %", "94.3–95.9", "97.3 % on held-out", "AEGIS-LINK ≈", "94 % live accuracy", "6 defences"]


@pytest.mark.parametrize("doc", ["README.md", "docs/DEMO_SCRIPT.md", "docs/EVIDENCE.md", "docs/BENCHMARK.md", "docs/SECURITY.md",
                                 "docs/ARCHITECTURE.md"])
def test_no_stale_figures(doc):
    text = _t(doc)
    for s in STALE:
        assert s not in text, f"{doc} still says {s!r}"


def test_mission_deck_fallback_matches_the_scenarios():
    """The deck keeps an offline copy of each mission's expected result; it must say what the server says."""
    sc = {s["id"]: s for s in _j("shared/scenarios.json")["scenarios"]}
    js = _t("intro/src/slides.js")
    found = {k: v.replace("\\'", "'") for k, v in re.findall(r"\{ id: '([a-z_]+)'.*?expected: '((?:[^'\\]|\\.)*)'", js)}
    assert set(found) == set(sc)
    for k, v in found.items():
        assert v == sc[k]["expected"], k
