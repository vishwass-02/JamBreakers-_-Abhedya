"""What happens when things go wrong: a crashing simulator, bad input, floods, disconnects, hostile web pages."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app import MAX_COMMANDS_PER_S, MAX_MESSAGE_BYTES, Client, create_app
from backend.auth import RedactTokens
from backend.config import Settings
from backend.fake_world import FakeArena


def _cfg(**kw):
    return dataclasses.replace(Settings(), world="fake", **kw)


def _until(ws, pred, limit=300):
    for _ in range(limit):
        m = json.loads(ws.receive_text())
        if pred(m):
            return m
    raise AssertionError("expected message never arrived")


class Flaky(FakeArena):
    """A world whose simulation step fails every third tick and whose command handler always fails."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.calls = 0

    def step(self, dt):
        self.calls += 1
        if self.calls % 3 == 0:
            raise RuntimeError("simulated crash in the radio model")
        return super().step(dt)

    def handle(self, cmd):
        raise RuntimeError("simulated crash in a command")


@pytest.fixture()
def flaky():
    app = create_app(_cfg(), world_factory=lambda c: Flaky(seed=c.seed))
    with TestClient(app) as c:
        yield c, app


def test_a_crashing_simulation_step_is_reported_and_the_server_keeps_going(flaky):
    c, app = flaky
    with c.websocket_connect("/ws") as ws:
        err = _until(ws, lambda m: m.get("type") == "error" and m.get("code") == "internal")
        assert "Simulation tick failed" in err["message"]
        _until(ws, lambda m: m.get("type") == "state")              # still broadcasting afterwards
    assert app.state.hub.ticks > 3


def test_a_crashing_command_answers_only_the_sender(flaky):
    c, _ = flaky
    with c.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "cmd", "id": "k1", "action": "clear_jammers"}))
        err = _until(ws, lambda m: m.get("req_id") == "k1")
        assert err["type"] == "error" and err["code"] == "internal" and "clear_jammers" in err["message"]


@pytest.fixture()
def client():
    app = create_app(_cfg())
    with TestClient(app) as c:
        yield c, app


@pytest.mark.parametrize("text,code", [
    ("not json at all", "bad_json"),
    ("[1, 2, 3]", "invalid_command"),
    ('{"type": "cmd"}', "invalid_command"),
    ('{"type": "cmd", "action": "self_destruct"}', "invalid_command"),
    ('{"type": "cmd", "action": "launch_jammer", "jammer_type": "barrage", "pos": [1e9, 0]}', "invalid_command"),
    ('{"type": "cmd", "action": "set_speed", "speed": "fast"}', "invalid_command"),
])
def test_bad_input_gets_a_clear_error_not_a_crash(client, text, code):
    c, _ = client
    with c.websocket_connect("/ws") as ws:
        ws.send_text(text)
        err = _until(ws, lambda m: m.get("type") == "error")
        assert err["code"] == code
        ws.send_text(json.dumps({"type": "cmd", "id": "ok1", "action": "set_speed", "speed": 2}))
        _until(ws, lambda m: m.get("req_id") == "ok1" and m.get("type") == "event")   # connection still works


def test_oversized_messages_are_refused(client):
    c, _ = client
    with c.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "cmd", "action": "clear_jammers", "pad": "x" * (MAX_MESSAGE_BYTES + 10)}))
        assert _until(ws, lambda m: m.get("type") == "error")["code"] == "too_large"


def test_command_floods_are_rate_limited(client):
    c, _ = client
    with c.websocket_connect("/ws") as ws:
        for i in range(MAX_COMMANDS_PER_S + 5):
            ws.send_text(json.dumps({"type": "cmd", "id": f"f{i}", "action": "set_speed", "speed": 1}))
        assert _until(ws, lambda m: m.get("type") == "error" and m.get("code") == "rate_limited")


def test_one_screen_disconnecting_does_not_affect_the_others(client):
    c, app = client
    hub = app.state.hub
    with c.websocket_connect("/ws") as a:
        with c.websocket_connect("/ws") as b:
            _until(b, lambda m: m.get("type") == "state")
            assert len(hub.clients) == 2
        _until(a, lambda m: m.get("type") == "state")             # a keeps receiving after b vanished
        for _ in range(50):
            if len(hub.clients) == 1:
                break
            a.receive_text()
        assert len(hub.clients) == 1


def test_a_slow_screen_drops_its_oldest_messages_instead_of_stalling():
    async def run():
        cl = Client(ws=None, maxsize=3)
        for i in range(10):
            cl.offer(str(i))
        return [await cl.get() for _ in range(3)], cl.dropped
    kept, dropped = asyncio.run(run())
    assert kept == ["7", "8", "9"] and dropped == 7


def test_a_slow_screen_never_loses_the_mission_report_or_its_own_answer():
    """Seen in the automated rehearsal: a slow screen's queue filled with world updates and the report was dropped."""
    def msg(kind, n):
        return json.dumps({"v": "1.0", "type": kind, "n": n}, separators=(",", ":"))

    async def run():
        cl = Client(ws=None, maxsize=5)
        cl.offer(msg("report", 0))
        cl.offer(msg("error", 1))
        for i in range(50):
            cl.offer(msg("state", i))
            cl.offer(msg("spectrum", i))
        return [json.loads(await cl.get()) for _ in range(len(cl.buf))]
    got = asyncio.run(run())
    kinds = [m["type"] for m in got]
    assert kinds[:2] == ["report", "error"] and len(got) == 5
    assert got[-1]["n"] == 49                          # and the newest updates are the ones kept


def test_a_web_page_from_another_site_cannot_drive_the_demo(client):
    """Cross-site WebSocket hijacking: any page open on the laptop could otherwise command it as the trusted laptop."""
    c, _ = client
    with c.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws:
        assert json.loads(ws.receive_text())["code"] == "bad_origin"
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
        assert e.value.code == 4403
    with c.websocket_connect("/ws", headers={"origin": "http://testserver"}) as ws:   # our own page: fine
        assert json.loads(ws.receive_text())["type"] == "hello"


def test_http_failures_are_clean(client):
    c, _ = client
    assert c.get("/api/report").status_code == 404                           # no finished mission yet
    assert c.get("/results/..%2F..%2Fbackend%2Fapp.py").status_code == 404     # no path traversal
    assert c.get("/results/nothing.json").status_code == 404
    assert c.post("/api/login", content=b"[1,2]", headers={"content-type": "application/json"}).status_code == 200
    assert c.post("/api/login", content=b"x" * 5000, headers={"content-type": "application/json"}).status_code == 413
    assert c.post("/api/login", json={}, headers={"origin": "https://evil.example"}).status_code == 403


def test_login_never_crashes_on_odd_bodies(tmp_path):
    from backend.auth import AuthStore
    users = tmp_path / "u.json"
    AuthStore(users).add_user("cmdr", "correct-horse", "commander")
    app = create_app(_cfg(auth=True, users_file=str(users)))
    with TestClient(app) as c:
        for body in (b"[1,2]", b'"text"', b"not json", b"null", b'{"username": 5, "password": null}'):
            r = c.post("/api/login", content=body, headers={"content-type": "application/json"})
            assert r.status_code in (401, 403), (body, r.status_code)


def test_session_tokens_never_reach_the_server_window():
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("10.0.0.5:51000", "GET", "/api/report?token=SECRET123&t=5", "1.1", 200), None)
    RedactTokens().filter(rec)
    line = rec.getMessage()
    assert "SECRET123" not in line and "token=***" in line
    ws = logging.LogRecord("uvicorn.error", logging.INFO, "", 0, '%s - "WebSocket %s" [accepted]',
                           ("10.0.0.5:51001", "/ws?token=SECRET123"), None)
    RedactTokens().filter(ws)
    assert "SECRET123" not in ws.getMessage()
