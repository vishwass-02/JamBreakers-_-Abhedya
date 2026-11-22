"""Sign-in: accounts, roles, the QR join code, lockout and the WebSocket gate."""
from __future__ import annotations

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app import create_app
from backend.auth import MAX_FAILS, AuthError, AuthStore, check_password, hash_password
from backend.config import Settings


@pytest.fixture()
def store(tmp_path):
    s = AuthStore(tmp_path / "users.json")
    s.add_user("cmdr", "correct-horse", "commander")
    s.add_user("judge", "watch-only-1", "observer")
    return s


def test_passwords_are_salted_and_never_stored(store, tmp_path):
    raw = (tmp_path / "users.json").read_text()
    assert "correct-horse" not in raw and "pbkdf2_sha256" in raw
    a, b = hash_password("same"), hash_password("same")
    assert a["hash"] != b["hash"] and check_password("same", a) and not check_password("other", a)


def test_first_run_creates_one_shared_account(tmp_path):
    s = AuthStore(tmp_path / "u.json")
    made = s.ensure_first_run()
    assert list(made) == ["abhedya"] and made["abhedya"][0] == "commander"
    assert all(len(pw) >= 12 for _, pw in made.values())
    assert s.ensure_first_run() == {}            # only once


def test_login_needs_password_and_join_code(store):
    code = store.join_code()
    with pytest.raises(AuthError) as e:
        store.login("cmdr", "wrong-password", "10.0.0.5", code)
    assert e.value.code == "bad_login"
    with pytest.raises(AuthError) as e:
        store.login("cmdr", "correct-horse", "10.0.0.5", "AAAAAA")
    assert e.value.code == "join_code"
    s = store.login("CMDR", "correct-horse", "10.0.0.5", code.lower())
    assert s["role"] == "commander" and store.check(s["token"]).can_command
    store.logout(s["token"])
    assert store.check(s["token"]) is None


def test_join_code_expires_and_sessions_expire(store):
    code = store.join_code(now=1000.0)
    assert store.join_code_ok(code, now=1000.0 + 60)
    assert not store.join_code_ok(code, now=1000.0 + 3 * 15 * 60)
    s = store.login("judge", "watch-only-1", "10.0.0.6", need_join_code=False, now=1000.0)
    assert store.check(s["token"], now=1000.0 + 3600) is not None
    assert store.check(s["token"], now=1000.0 + 13 * 3600) is None


def test_lockout_after_repeated_wrong_passwords(store):
    for _ in range(MAX_FAILS):
        with pytest.raises(AuthError):
            store.login("cmdr", "nope-nope", "10.0.0.7", need_join_code=False, now=50.0)
    with pytest.raises(AuthError) as e:
        store.login("cmdr", "correct-horse", "10.0.0.7", need_join_code=False, now=55.0)
    assert e.value.code == "locked"
    # a different device is not locked out, and the lock lifts after a minute
    assert store.login("cmdr", "correct-horse", "10.0.0.8", need_join_code=False, now=55.0)
    assert store.login("cmdr", "correct-horse", "10.0.0.7", need_join_code=False, now=200.0)


def test_changing_a_password_signs_that_user_out(store):
    s = store.login("judge", "watch-only-1", "10.0.0.9", need_join_code=False)
    store.add_user("judge", "a-new-password", "observer")
    assert store.check(s["token"]) is None


def test_weak_or_odd_accounts_are_refused(store):
    with pytest.raises(ValueError):
        store.add_user("x", "long-enough-pw", "observer")
    with pytest.raises(ValueError):
        store.add_user("neha", "short", "observer")
    with pytest.raises(ValueError):
        store.add_user("neha", "long-enough-pw", "admin")


@pytest.fixture()
def secured(tmp_path):
    users = tmp_path / "users.json"
    s = AuthStore(users)
    s.add_user("cmdr", "correct-horse", "commander")
    s.add_user("judge", "watch-only-1", "observer")
    cfg = dataclasses.replace(Settings(), auth=True, world="fake", users_file=str(users))
    app = create_app(cfg)
    with TestClient(app) as c:      # TestClient's address is 'testclient': a remote device, not the laptop
        yield c, app


def _login(c, app, user, pw):
    code = app.state.auth.join_code()
    r = c.post("/api/login", json={"username": user, "password": pw, "join_code": code})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_remote_devices_must_sign_in(secured):
    c, app = secured
    assert c.get("/api/scenarios").status_code == 401
    assert c.get("/api/report").status_code == 401
    assert c.get("/api/me").status_code == 401
    assert c.get("/api/lan").status_code == 401
    # join code is off by default: any device on the Wi-Fi signs in with the user ID and password alone
    assert c.post("/api/login", json={"username": "cmdr", "password": "correct-horse"}).status_code == 200
    assert c.get("/app/").status_code in (200, 404) and c.get("/login").status_code == 200   # the shell itself is public
    tok = _login(c, app, "cmdr", "correct-horse")
    h = {"Authorization": f"Bearer {tok}"}
    assert c.get("/api/scenarios", headers=h).status_code == 200
    assert c.get("/api/me", headers=h).json() == {"user": "cmdr", "role": "commander", "local": False, "auth": True}
    assert c.get("/api/lan", headers=h).status_code == 403     # the join code is shown only on the laptop
    assert c.get(f"/api/scenarios?token={tok}").status_code == 200
    r = c.get("/api/me", headers=h)
    assert r.headers["x-content-type-options"] == "nosniff"


def test_websocket_rejects_strangers(secured):
    c, _ = secured
    with c.websocket_connect("/ws") as ws:
        m = json.loads(ws.receive_text())
        assert m["type"] == "error" and m["code"] == "auth_required"
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
        assert e.value.code == 4401


def _first(ws, kind, limit=200):
    for _ in range(limit):
        m = json.loads(ws.receive_text())
        if m.get("type") == kind:
            return m
    raise AssertionError(f"no {kind}")


def test_observer_can_watch_but_not_command(secured):
    c, app = secured
    tok = _login(c, app, "judge", "watch-only-1")
    with c.websocket_connect(f"/ws?token={tok}") as ws:
        assert json.loads(ws.receive_text())["type"] == "hello"
        ws.send_text(json.dumps({"type": "cmd", "id": "x1", "action": "set_speed", "speed": 2}))
        err = _first(ws, "error")
        assert err["code"] == "forbidden" and err["req_id"] == "x1"


def test_commander_can_command(secured):
    c, app = secured
    tok = _login(c, app, "cmdr", "correct-horse")
    with c.websocket_connect(f"/ws?token={tok}") as ws:
        assert json.loads(ws.receive_text())["type"] == "hello"
        ws.send_text(json.dumps({"type": "cmd", "id": "c1", "action": "set_speed", "speed": 2}))
        for _ in range(200):
            m = json.loads(ws.receive_text())
            assert not (m.get("type") == "error" and m.get("req_id") == "c1"), m
            if m.get("type") == "event" and m.get("req_id") == "c1":
                break
        else:
            raise AssertionError("command was not acknowledged")


def test_a_second_server_start_does_not_create_different_accounts(tmp_path):
    a, b = AuthStore(tmp_path / "users.json"), AuthStore(tmp_path / "users.json")   # both loaded before either saved
    made = a.ensure_first_run()
    assert made and b.ensure_first_run() == {}            # b re-reads the file instead of overwriting a's accounts
    pw = made["abhedya"][1]
    assert AuthStore(tmp_path / "users.json").login("abhedya", pw, "10.0.0.1", need_join_code=False)
    assert not list(tmp_path.glob("*.tmp"))


def test_password_changed_while_server_runs(tmp_path):
    """A running server must accept a password changed on disk (python -m backend.users), without a restart."""
    import json
    import os
    from backend.auth import AuthStore, AuthError, hash_password
    f = tmp_path / "users.json"
    f.write_text(json.dumps({"users": {"judge": {"name": "judge", "role": "observer", **hash_password("old-pass-1")}}}))
    store = AuthStore(f)
    store.login("judge", "old-pass-1", "10.0.0.5", need_join_code=False)
    f.write_text(json.dumps({"users": {"judge": {"name": "judge", "role": "observer", **hash_password("judge123")}}}))
    st = f.stat()
    os.utime(f, (st.st_atime, st.st_mtime + 5))
    assert store.login("judge", "judge123", "10.0.0.6", need_join_code=False)["role"] == "observer"
    try:
        store.login("judge", "old-pass-1", "10.0.0.7", need_join_code=False)
        raise AssertionError("old password still accepted")
    except AuthError as e:
        assert e.code == "bad_login"
