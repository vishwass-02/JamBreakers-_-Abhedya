"""Spectrum sources (ai/sources.py), replay through the CNN, and the mission log."""
from __future__ import annotations

import numpy as np
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from ai.features import NOISE_BIN_DBM
from ai.sources import BIN_CENTRES, ROWS, Recording, evaluate, list_recordings, parse_hackrf_sweep, save_recording, to_our_bins
from backend.app import create_app
from backend.commands import parse_command
from backend.config import Settings
from backend.protocol import load_schema
from sim.world import SimArena

V = Draft202012Validator(load_schema())


def test_hackrf_sweep_lines_become_one_row_per_sweep():
    lines = []
    for sweep in range(3):
        for lo in range(2400, 2490, 10):                 # 10 MHz per line, 16 bins of 625 kHz
            vals = ", ".join("-70.0" if 2440 <= lo < 2450 else "-95.0" for _ in range(16))
            lines.append(f"2026-11-21, 10:00:0{sweep}.000, {lo * 1_000_000}, {(lo + 10) * 1_000_000}, 625000.00, 20, {vals}")
    psd, freqs, t = parse_hackrf_sweep(["date, time, hz_low, hz_high, hz_bin_width, num_samples, dB"] + lines)
    assert psd.shape == (3, 144) and len(t) == 3
    rows = to_our_bins(psd, freqs)
    assert rows.shape == (3, 128)
    hot = rows[0, (BIN_CENTRES > 2441) & (BIN_CENTRES < 2449)]
    cold = rows[0, BIN_CENTRES < 2430]
    assert abs(float(np.median(cold)) - NOISE_BIN_DBM) < 1.0      # floor aligned to our receiver
    assert float(hot.min()) - float(cold.max()) > 20             # the jammed 10 MHz still stands out


def test_recording_roundtrip_and_windows(tmp_path):
    psd = np.full((40, 64), -95.0, np.float32)
    psd[:, 20:30] = -60.0
    p = save_recording(tmp_path / "r.npz", psd, np.linspace(2400, 2490, 64), labels=["barrage"] * 40, source="unit test")
    rec = Recording(p)
    assert rec.rows.shape == (40, 128) and rec.n_windows == (40 - ROWS) // (ROWS // 2) + 1
    w, lab, _ = rec.window(0)
    assert w.shape == (ROWS, 128) and lab == "barrage"
    assert rec.info()["source"] == "unit test"


def test_shipped_captures_are_marked_simulated_and_classified():
    infos = {r["file"]: r for r in list_recordings()}
    assert infos["sim_capture_sweep.npz"]["simulated"] is True
    assert evaluate("recordings/sim_capture_sweep.npz")["accuracy_pct"] > 90
    novel = evaluate("recordings/sim_capture_novel.npz")["verdicts_by_label"]["unknown"]
    assert novel.get("unknown", 0) > 0.9 * sum(novel.values())   # the never-seen comb is called unknown


def test_arena_replays_a_recording_through_the_cnn():
    a = SimArena(Settings())
    out = a.handle(parse_command({"type": "cmd", "action": "replay", "name": "sim_capture_sweep"}))
    assert "replays" in out[0]["text"]
    msgs = []
    for _ in range(8):
        msgs += a.step(0.05)
    rp = [m for m in msgs if m["type"] == "replay"]
    assert rp and rp[-1]["verdict"] == "sweep" and rp[-1]["stats"]["correct"] >= 1
    assert not [e for m in rp for e in V.iter_errors(m)]
    off = a.handle(parse_command({"type": "cmd", "action": "replay", "name": None}))
    assert off[0]["active"] is False and a.replay is None
    err = a.handle(parse_command({"type": "cmd", "action": "replay", "name": "missing_file"}))
    assert err[0]["type"] == "error"


def test_mission_log_records_decisions_and_downloads():
    a = SimArena(Settings())
    a.handle(parse_command({"type": "cmd", "action": "launch_jammer", "jammer_type": "follower", "pos": [300, 150]}))
    a.handle(parse_command({"type": "cmd", "action": "security_test", "test": "tamper"}))
    for _ in range(80):
        a.step(0.05)
    kinds = {r["kind"] for r in a.log.rows}
    assert {"kpi", "event", "security", "decision"} <= kinds
    csv_text = a.log.to_csv()
    assert csv_text.startswith("# ABHEDYA mission log") and "sim_time_s,world,kind" in csv_text
    with TestClient(create_app()) as c:
        r = c.get("/api/mission-log")
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
        assert c.get("/api/mission-log?format=json").json()["summary"]["label"].startswith("Live arena")
        assert any(x["file"] == "sim_capture_sweep.npz" for x in c.get("/api/recordings").json())
