"""10x speed and the downloadable end-of-mission report (GET /api/report)."""
from __future__ import annotations

import re

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.commands import parse_command
from backend.config import Settings
from sim.report_html import build_report_html
from sim.world import SimArena


def cmd(**k):
    return parse_command({"type": "cmd", **k})


def test_speed_10_is_accepted_and_others_are_not():
    assert cmd(action="set_speed", speed=10).speed == 10
    for bad in (3, 5, 20):
        with pytest.raises(ValidationError):
            cmd(action="set_speed", speed=bad)


@pytest.fixture(scope="module")
def finished():
    """Coastal watch run to the end at 10x, the way a user would."""
    a = SimArena(Settings())
    a.handle(cmd(action="load_scenario", name="coastal_watch"))
    a.handle(cmd(action="play"))
    a.handle(cmd(action="set_speed", speed=10))
    for _ in range(6000):
        a.step(0.05)
        if a.last_report:
            break
    assert a.last_report, "scenario never finished"
    return a


def test_mission_finishes_at_10x_with_a_report_snapshot(finished):
    snap = finished.last_report
    assert snap["speed"] == 10 and snap["msg"]["status"] == "done"
    assert snap["msg"]["duration_s"] >= snap["scenario"]["duration_s"]
    assert any(r["kind"] == "kpi" for r in snap["rows"]) and snap["csv"]


def test_report_html_is_self_contained_and_labelled_simulated(finished):
    h = build_report_html(finished.last_report)
    assert h.startswith("<!DOCTYPE html>") and "Coastal watch" in h
    assert "Simulated data" in h and "not measurements from radio hardware" in h
    assert "<svg" in h and "AI OFF" in h and "AI ON" in h and "window.print()" in h
    assert "data:text/csv;base64," in h
    assert not re.search(r'(src|href)="https?://', h), "report must open offline"
    on = finished.last_report["msg"]["ai_on"]["delivery_attack_pct"]
    assert f"{on}%" in h or f"{int(on)}%" in h


def test_report_endpoint_downloads_the_last_mission(finished):
    app = create_app(Settings())
    with TestClient(app) as c:
        hub = app.state.hub
        if not hasattr(hub.world, "last_report"):
            pytest.skip("fake world")
        hub.world.last_report = None
        assert c.get("/api/report").status_code == 404
        hub.world.last_report = finished.last_report
        r = c.get("/api/report")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
        cd = r.headers["content-disposition"]
        assert cd.startswith("attachment") and re.search(r'filename="ABHEDYA_coastal_watch_\d{8}_\d{4}\.html"', cd)
        assert "Coastal watch" in r.text
        assert c.get("/api/report?download=0").headers["content-disposition"].startswith("inline")


def test_border_convoy_barrage_hurts_fixed_hopping_but_not_the_ai():
    """Expected: fixed hopping ≈ 30–40 %, AEGIS ≈ 90 %+ (was 99.7 % vs 100 % when the ridge jammer was too weak)."""
    a = SimArena(Settings())
    a.handle(cmd(action="load_scenario", name="border_convoy"))
    a.handle(cmd(action="play"))
    a.handle(cmd(action="set_speed", speed=10))
    for _ in range(6000):
        a.step(0.05)
        if a.last_report:
            break
    m = a.last_report["msg"]
    assert 25 <= m["ai_off"]["delivery_attack_pct"] <= 45
    assert m["ai_on"]["delivery_attack_pct"] >= 90


def test_a_screen_that_reconnects_after_the_mission_still_gets_the_result(finished):
    """Seen in the automated rehearsal: a slow screen lost its connection in the last seconds and never saw the
    report. Screens that connect while a result is on screen get it right after hello; it clears on the next run."""
    app = create_app(Settings())
    with TestClient(app) as c:
        hub = app.state.hub
        if not hasattr(hub.world, "finished_report"):
            pytest.skip("fake world")
        hub.world.finished_report = finished.last_report["msg"]
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"
            m = ws.receive_json()
            assert m["type"] == "report" and m["scenario"] == "coastal_watch"
            ws.send_json({"type": "cmd", "action": "exit_scenario"})
            for _ in range(200):
                if ws.receive_json().get("type") == "event":
                    break
        assert hub.world.finished_report is None
