"""Phase 0 tests: message contract, server, fake world. Run: pytest -q"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from backend.app import create_app
from backend.commands import parse_command
from backend.config import Settings
from backend.fake_world import FakeArena
from backend.protocol import JAMMER_TYPES, load_schema

SCHEMA = load_schema()
VALIDATOR = Draft202012Validator(SCHEMA)
Draft202012Validator.check_schema(SCHEMA)


def assert_valid(msg: dict) -> None:
    errors = sorted(VALIDATOR.iter_errors(msg), key=lambda e: e.path)
    assert not errors, f"{msg.get('type')} invalid: {errors[0].message} at {list(errors[0].path)}"


@pytest.fixture(params=["sim", "fake"])
def client(request, monkeypatch):
    monkeypatch.setenv("AEGIS_WORLD", request.param)
    app = create_app(Settings())
    with TestClient(app) as c:
        c.world_kind = request.param
        yield c


def recv_until(ws, pred, limit=400):
    for _ in range(limit):
        m = ws.receive_json()
        if pred(m):
            return m
    raise AssertionError("expected message not received")


# ---------- HTTP ----------
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_schema_endpoint_matches_file(client):
    assert client.get("/api/schema").json() == SCHEMA


def test_lan_endpoint(client):
    body = client.get("/api/lan").json()
    assert body["port"] == Settings().port and isinstance(body["ips"], list)   # 8000 unless AEGIS_PORT says otherwise


def test_console_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "ABHEDYA" in r.text


# ---------- WebSocket ----------
def test_hello_then_state_at_20hz(client):
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["v"] == "1.0"
        assert_valid(hello)
        recv_until(ws, lambda m: m["type"] == "state")  # sync to tick
        t0, n = time.perf_counter(), 0
        while n < 20:
            if ws.receive_json()["type"] == "state":
                n += 1
        elapsed = time.perf_counter() - t0
        assert 0.75 < elapsed < 1.5, f"20 state messages took {elapsed:.2f}s (expected ~1.0s)"


def test_every_message_matches_schema(client):
    """Drive every command and validate everything the server sends back."""
    cmds = [{"action": "launch_jammer", "jammer_type": "barrage", "pos": [99999, 0]}]   # off the map -> error message
    cmds += [{"action": "launch_jammer", "jammer_type": t, "pos": [100 + 50 * i, 80]} for i, t in enumerate(JAMMER_TYPES)]
    cmds += [{"action": "toggle_ai"}, {"action": "toggle_ai", "enabled": True},
             {"action": "set_view", "view": "side_by_side"}, {"action": "run_benchmark", "seed": 7}]
    cmds += [{"action": "security_test", "test": t} for t in ["tamper", "spoof", "replay", "eavesdrop"]]
    cmds += [{"action": "pause"}, {"action": "play"}, {"action": "set_speed", "speed": 4},
             {"action": "load_scenario", "name": "border_convoy"}, {"action": "clear_jammers"},
             {"action": "set_view", "view": "single"}, {"action": "restart"}]
    seen: set[str] = set()
    worlds: set[str] = set()
    with client.websocket_connect("/ws") as ws:
        for i, c in enumerate(cmds):
            ws.send_json({"type": "cmd", "id": f"r{i}", "player": "tester", **c})
            for _ in range(12):
                m = ws.receive_json()
                assert_valid(m)
                seen.add(m["type"])
                if m["type"] == "state":
                    worlds.add(m["world"])
        if client.world_kind == "fake":  # let the placeholder benchmark finish
            recv_until(ws, lambda m: (assert_valid(m) or True) and m["type"] == "benchmark" and m["status"] == "done", 200)
        else:  # let the real world run a while under attack so decisions appear
            ws.send_json({"type": "cmd", "action": "launch_jammer", "jammer_type": "deceptive", "pos": [150, 80]})
            for _ in range(200):
                m = ws.receive_json()
                assert_valid(m)
                seen.add(m["type"])
    expected = {"hello", "state", "spectrum", "event", "score", "error", "security"}
    expected |= set() if client.world_kind == "fake" else {"decision"}
    assert expected <= seen, expected - seen
    assert {"main", "ai_off", "ai_on"} <= worlds


def test_bad_json_and_invalid_commands_go_to_sender_only(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_text("not json")
        e = recv_until(ws, lambda m: m["type"] == "error")
        assert e["code"] == "bad_json"

        ws.send_json({"type": "cmd", "id": "x1", "action": "fly_to_moon"})
        e = recv_until(ws, lambda m: m["type"] == "error")
        assert e["code"] == "invalid_command" and e["req_id"] == "x1"

        ws.send_json({"type": "cmd", "id": "x2", "action": "launch_jammer", "jammer_type": "barrage", "pos": [99999, 0]})
        e = recv_until(ws, lambda m: m["type"] == "error")
        assert e["code"] == "invalid_command" and "outside map" in e["message"]

        ws.send_json({"type": "cmd", "id": "x3", "action": "set_speed", "speed": 3})
        assert recv_until(ws, lambda m: m["type"] == "error")["req_id"] == "x3"


def test_commands_are_shared_with_every_screen(client):
    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        a.send_json({"type": "cmd", "id": "j1", "action": "launch_jammer", "jammer_type": "barrage", "pos": [10, 10]})
        ev = recv_until(b, lambda m: m["type"] == "event" and m.get("req_id") == "j1")
        assert "barrage" in ev["text"]
        st = recv_until(b, lambda m: m["type"] == "state" and m["jammers"])
        assert st["jammers"][0]["type"] == "barrage"


# ---------- command models stay in step with the JSON schema ----------
GOOD = [
    {"type": "cmd", "action": "launch_jammer", "jammer_type": "sweep", "pos": [0, 0], "power_dbm": 30},
    {"type": "cmd", "action": "clear_jammers"},
    {"type": "cmd", "action": "toggle_ai", "enabled": False},
    {"type": "cmd", "action": "set_view", "view": "side_by_side"},
    {"type": "cmd", "action": "run_benchmark", "seed": 1, "duration_s": 30},
    {"type": "cmd", "action": "security_test", "test": "replay"},
    {"type": "cmd", "action": "load_scenario", "name": "urban_patrol"},
    {"type": "cmd", "action": "exit_scenario"},
    {"type": "cmd", "action": "set_speed", "speed": 2},
    {"type": "cmd", "action": "restart", "id": "abc", "player": "judge"},
]
BAD = [
    {"type": "cmd", "action": "launch_jammer", "jammer_type": "laser", "pos": [0, 0]},
    {"type": "cmd", "action": "launch_jammer", "jammer_type": "sweep"},
    {"type": "cmd", "action": "set_view", "view": "triple"},
    {"type": "cmd", "action": "security_test", "test": "nuke"},
    {"type": "cmd", "action": "set_speed", "speed": 3},
    {"type": "cmd", "action": "load_scenario"},
    {"type": "command", "action": "clear_jammers"},
]


@pytest.mark.parametrize("cmd", GOOD)
def test_good_commands_pass_both(cmd):
    parse_command(cmd)
    assert_valid(cmd)


@pytest.mark.parametrize("cmd", BAD)
def test_bad_commands_fail_both(cmd):
    with pytest.raises(ValidationError):
        parse_command(cmd)
    assert list(VALIDATOR.iter_errors(cmd)), f"schema accepted {json.dumps(cmd)}"


# ---------- fake world behaviour (so UI work has something sensible to react to) ----------
def _run(arena: FakeArena, seconds: float):
    out = []
    for _ in range(int(seconds * 20)):
        out.extend(arena.step(0.05))
    return out


def _delivery(msgs, world="main"):
    return [m for m in msgs if m["type"] == "state" and m["world"] == world][-1]["kpis"]["delivery_pct"]


def test_fake_jammer_hurts_and_ai_reacts():
    arena = FakeArena(seed=1)
    arena.handle(parse_command({"type": "cmd", "action": "toggle_ai", "enabled": False}))
    clean = _delivery(_run(arena, 1))
    arena.handle(parse_command({"type": "cmd", "action": "launch_jammer", "jammer_type": "random", "pos": [40, 30]}))
    jammed = _run(arena, 2)
    assert min(m["kpis"]["delivery_pct"] for m in jammed if m["type"] == "state") < clean - 20
    arena.handle(parse_command({"type": "cmd", "action": "toggle_ai", "enabled": True}))
    msgs = _run(arena, 2)
    assert any(m["type"] == "decision" for m in msgs)


def test_fake_side_by_side_same_seed_ai_differs():
    arena = FakeArena(seed=3)
    arena.handle(parse_command({"type": "cmd", "action": "set_view", "view": "side_by_side"}))
    arena.handle(parse_command({"type": "cmd", "action": "launch_jammer", "jammer_type": "deceptive", "pos": [40, 30]}))
    msgs = _run(arena, 3)
    off = [m for m in msgs if m["type"] == "state" and m["world"] == "ai_off"]
    on = [m for m in msgs if m["type"] == "state" and m["world"] == "ai_on"]
    assert len(off) == len(on) == 60
    assert not off[-1]["ai_enabled"] and on[-1]["ai_enabled"]
    assert on[-1]["kpis"]["hops"] > 0 and off[-1]["kpis"]["hops"] == 0


def test_phone_app_and_judges_screen_are_served(client):
    r = client.get("/app/")
    assert r.status_code == 200 and "ABHEDYA" in r.text and "manifest.webmanifest" in r.text
    assert client.get("/app/manifest.webmanifest").json()["start_url"] == "/app/"
    sw = client.get("/app/sw.js")
    assert sw.status_code == 200 and "caches" in sw.text
    assert client.get("/app/icons/icon-512.png").status_code == 200
    d = client.get("/demo", follow_redirects=False)
    assert d.status_code in (302, 307) and d.headers["location"] == "/viewer/demo.html"
    page = client.get("/viewer/demo.html")
    assert page.status_code == 200 and "/app/" in page.text and "qrcode" in page.text
    assert client.get("/viewer/vendor/qrcode.js").status_code == 200
    lan = client.get("/api/lan").json()
    assert all(u.endswith("/app/") for u in lan["app_urls"])


def test_mission_deck_is_served_and_links_to_the_viewer(client):
    import re
    for path in ("/intro", "/start", "/missions"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code in (302, 307) and r.headers["location"] == "/intro/"
    page = client.get("/intro/")
    assert page.status_code == 200 and "Mission deck" in page.text and page.headers["cache-control"] == "no-cache"
    js = re.search(r'src="(/intro/assets/[^"]+\.js)"', page.text).group(1)
    bundle = client.get(js).text
    assert "/viewer/" in bundle and "?start=" in bundle          # every slide launches the viewer
    viewer = client.get("/viewer/app.js").text
    assert "deepStart" in viewer and "load_scenario" in viewer      # and the viewer starts what it was given


def test_qr_prefers_the_hotspot_and_real_wifi_over_virtual_adapters():
    from backend.app import _lan_rank
    ips = ["172.24.80.1", "192.168.56.1", "10.110.21.15", "192.168.137.1", "192.168.1.7"]
    assert sorted(ips, key=_lan_rank)[:2] == ["192.168.137.1", "192.168.1.7"]
    assert sorted(ips, key=_lan_rank)[-2:] == ["172.24.80.1", "192.168.56.1"]
