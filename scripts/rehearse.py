"""Automated demo rehearsal: drives every screen the way the presenter does, with no one touching anything.

    pip install playwright && python -m playwright install chromium      # once
    python scripts/rehearse.py --rounds 3                                 # starts its own server on port 8191

Each round:
  1. mission deck /intro/      loads, draws its 3D world, steps through all 8 slides
  2. 3D view, judges' screen, debug console: load without a single browser error
  3. phone app over the network address: QR join code + sign-in as an observer (watch only) and as a commander;
     the observer's command is refused, the commander's works
  4. all 7 missions, one after another, at 10x: each must finish, show its result card and download its report
Any JavaScript error, failed request, wrong result or timeout fails the round. A summary table follows.
It uses its own temporary accounts and never touches data/users.json.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
MISSIONS = [m for m in os.environ.get("REHEARSE_MISSIONS", "").split(",") if m] or ["border_convoy", "urban_patrol", "coastal_watch", "forest_ambush", "airbase_spoof", "relief_camp", "drone_delivery"]
IGNORE = ("GPU stall", "WebGL", "GL_", "favicon", "Automatic fallback to software WebGL", "[.WebGL",
          "status of 401")          # the phone asks /api/me before sign-in: a 401 there is the expected answer
SLOW = int(os.environ.get("REHEARSE_SLOW", "1"))   # x2-x3 on machines without a graphics card


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def start_server(port: int, users: Path) -> subprocess.Popen:
    env = dict(os.environ, AEGIS_PORT=str(port), AEGIS_USERS_FILE=str(users), AEGIS_AUTH="1")
    log = open(users.parent / "server.log", "w")      # a file, not a pipe: an unread pipe fills up and freezes the server
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", str(port)],
                         cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            return p
        except Exception:
            time.sleep(0.5)
    p.kill()
    raise SystemExit("server did not start")


class Round:
    def __init__(self, browser, base: str, lan: str, accounts: dict):
        self.b, self.base, self.lan, self.acc = browser, base, lan, accounts
        self.errors: list[str] = []
        self.steps: list[tuple[str, bool, str]] = []

    def page(self, **kw):
        pg = self.b.new_page(**kw)
        self.last = pg
        pg.on("pageerror", lambda e: self.errors.append(f"{pg.url}: {e}"))
        pg.on("console", lambda m: m.type == "error" and not any(x in m.text for x in IGNORE)
              and self.errors.append(f"{pg.url}: console: {m.text[:160]}"))
        pg.on("requestfailed", lambda r: "favicon" not in r.url and self.errors.append(f"request failed: {r.url[:120]}"))
        return pg

    def step(self, name: str, fn):
        t0, before = time.time(), len(self.errors)
        try:
            detail = fn() or ""
            ok = len(self.errors) == before
            if not ok:
                detail = "; ".join(self.errors[before:])[:300]
        except Exception as e:      # noqa: BLE001 - every failure is reported, none stops the round
            ok, detail = False, f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
            try:                    # leave evidence: what the screen showed when it failed
                shot = Path(tempfile.gettempdir()) / f"rehearse_fail_{name.split(':')[0].replace(' ', '_')}.png"
                self.last.screenshot(path=str(shot))
                state = self.last.evaluate("[location.href, document.querySelector('.toast')?.textContent, "
                                           "document.getElementById('h-speed')?.textContent, "
                                           "document.getElementById('hud-text')?.textContent, "
                                           "document.getElementById('hud-time')?.textContent]")
                detail += f" | screen {shot} | state {state}"
            except Exception:       # noqa: BLE001
                pass
        self.steps.append((name, ok, f"{time.time() - t0:5.1f}s {detail}"))
        print(f"  [{'ok ' if ok else 'FAIL'}] {name:34s} {self.steps[-1][2]}", flush=True)

    # ---- the screens
    def deck(self):
        pg = self.page(viewport={"width": 1440, "height": 860})
        pg.goto(f"{self.base}/intro/")
        pg.wait_for_selector("canvas", timeout=20000)
        pg.wait_for_timeout(3500)                                 # splash + first slide
        for _ in range(7):
            pg.keyboard.press("ArrowRight")
            pg.wait_for_timeout(3000)                             # globe-spin transition
        pg.close()

    def screens(self):
        for path in ("/viewer/", "/demo", "/"):
            pg = self.page(viewport={"width": 1440, "height": 860})
            pg.goto(self.base + path)
            pg.wait_for_timeout(4000)
            pg.close()

    def phone(self):
        code = json.loads(urllib.request.urlopen(f"{self.base}/api/lan").read())["join_code"]
        out = []
        for user in ("judge", "commander"):
            pg = self.page(viewport={"width": 390, "height": 800})
            pg.goto(f"{self.lan}/app/#j={code}")
            pg.wait_for_selector("#li-u", timeout=15000)
            pg.fill("#li-u", user)
            pg.fill("#li-p", self.acc[user])
            pg.click("button.primary")
            pg.wait_for_function("document.querySelector('.pill.conn')?.textContent === 'Live'", timeout=15000)
            # Send a real command over this phone's own signed-in session. (The phone's AI OFF/ON buttons only switch
            # which world the phone displays while the demo shows both side by side, so they send nothing.)
            toast = pg.evaluate("""async () => {
                const s = JSON.parse(localStorage.getItem('abhedya_session'));
                const ws = new WebSocket(`ws://${location.host}/ws?token=${encodeURIComponent(s.token)}`);
                return await new Promise((res) => {
                    ws.onopen = () => ws.send(JSON.stringify({type: 'cmd', id: 'r1', action: 'set_speed', speed: 1}));
                    ws.onmessage = (e) => { const m = JSON.parse(e.data);
                        if (m.req_id === 'r1') { ws.close(); res(m.type === 'error' ? m.message : 'accepted: ' + m.text); } };
                    setTimeout(() => res('no answer'), 6000);
                });
            }""")
            if user == "judge" and "view-only" not in toast:
                raise AssertionError(f"observer command was not refused: {toast!r}")
            if user == "commander" and not toast.startswith("accepted"):
                raise AssertionError(f"commander was refused: {toast!r}")
            out.append(f"{user}: {toast[:40]!r}")
            pg.close()
        return "; ".join(out)

    def missions(self):
        res = []
        pg = self.page(viewport={"width": 1440, "height": 860}, accept_downloads=True)
        for m in MISSIONS:
            pg.goto(f"{self.base}/viewer/?start={m}{'&q=low' if SLOW > 1 else ''}")
            pg.wait_for_function("(() => { const e = document.getElementById('h-speed'); return e && e.getBoundingClientRect().width > 0; })()",
                                 timeout=20000 * SLOW)
            pg.wait_for_timeout(2200)                             # auto-play starts 1.6 s after loading
            for _ in range(4):
                if pg.inner_text("#h-speed").strip() == "10×":
                    break
                pg.click("#h-speed")
                pg.wait_for_timeout(250)
            pg.wait_for_function("!document.getElementById('report').classList.contains('hidden')", timeout=90000 * SLOW)
            off = pg.inner_text("#rep-off")
            on = pg.inner_text("#rep-on")
            with pg.expect_download(timeout=15000) as dl:
                pg.click("#rep-download")
            path = dl.value.path()
            html = Path(path).read_text(encoding="utf-8")
            if "Simulated data" not in html or "<svg" not in html:
                raise AssertionError(f"{m}: report incomplete")
            res.append(f"{m.split('_')[0]} {off.split()[0] if off else '?'}->{on.split()[0] if on else '?'}")
        pg.close()
        return " | ".join(res)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--port", type=int, default=8191)
    ap.add_argument("--only", default="deck,screens,phone,missions", help="comma-separated steps to run")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright
    from backend.auth import AuthStore, make_password
    tmp = Path(tempfile.mkdtemp(prefix="abhedya_rehearse_"))
    acc = {"commander": make_password(), "judge": make_password()}
    st = AuthStore(tmp / "users.json")
    st.add_user("commander", acc["commander"], "commander")
    st.add_user("judge", acc["judge"], "observer")
    srv = start_server(a.port, tmp / "users.json")
    base, lan = f"http://localhost:{a.port}", f"http://{lan_ip()}:{a.port}"
    summary = []
    try:
        with sync_playwright() as p:
            exe = os.environ.get("CHROME")
            browser = p.chromium.launch(executable_path=exe, args=["--no-proxy-server", "--use-angle=swiftshader",
                                                                   "--enable-unsafe-swiftshader"]) if exe else \
                p.chromium.launch(args=["--no-proxy-server"])
            for r in range(1, a.rounds + 1):
                print(f"\nRound {r} of {a.rounds}")
                rd = Round(browser, base, lan, acc)
                only = a.only.split(",")
                for key, name, fn in (("deck", "mission deck: 8 slides", rd.deck), ("screens", "3D view, judges' screen, console", rd.screens),
                                      ("phone", "phone: QR + sign-in, roles", rd.phone), ("missions", "7 missions at 10x + reports", rd.missions)):
                    if key in only:
                        rd.step(name, fn)
                summary.append(all(ok for _, ok, _ in rd.steps))
            browser.close()
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except subprocess.TimeoutExpired:
            srv.kill()
    passed = sum(summary)
    print(f"\n{passed} of {len(summary)} rounds passed with no errors. Server log: {tmp / 'server.log'}")
    return 0 if passed == len(summary) else 1


if __name__ == "__main__":
    sys.exit(main())
