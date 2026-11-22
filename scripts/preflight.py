"""Demo-day pre-flight check. Run it on the demo laptop before presenting:

    check_demo.bat            (Windows)      or      python scripts/preflight.py

Each line is PASS, WARN (works, but fix it if you can) or FAIL (fix before the demo). Nothing is changed.
"""
from __future__ import annotations

import importlib
import json
import os
import platform
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
results: list[tuple[str, str, str]] = []


def report(level: str, what: str, detail: str = "") -> None:
    results.append((level, what, detail))
    mark = {"PASS": "  ok ", "WARN": " WARN", "FAIL": " FAIL"}[level]
    print(f"[{mark}] {what}" + (f"\n         {detail}" if detail else ""), flush=True)


def check_python() -> None:
    v = sys.version_info
    if v < (3, 10):
        report("FAIL", f"Python {v.major}.{v.minor}", "ABHEDYA needs Python 3.10 or newer.")
    else:
        report("PASS", f"Python {v.major}.{v.minor}.{v.micro}")
    missing = []
    for mod in ("fastapi", "uvicorn", "websockets", "pydantic", "numpy", "scipy", "cryptography"):
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        report("FAIL", "Python packages missing: " + ", ".join(missing), "Run run_windows.bat once (it installs them into .venv).")
    else:
        report("PASS", "All Python packages installed")


def check_builds() -> None:
    for name, p in (("Phone app", ROOT / "mobile" / "dist" / "index.html"), ("Mission deck", ROOT / "intro" / "dist" / "index.html"),
                    ("3D view", ROOT / "viewer3d" / "index.html"), ("AI weights", ROOT / "ai" / "weights" / "cnn_v1.npz")):
        if p.exists():
            report("PASS", f"{name} present")
        else:
            report("FAIL" if name != "AI weights" else "WARN", f"{name} missing ({p.relative_to(ROOT)})")


def check_server() -> None:
    port = int(os.environ.get("AEGIS_PORT", "8000"))
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            h = json.loads(r.read())
        report("PASS", f"ABHEDYA is running on port {port} (version {h.get('version')}, {h.get('clients')} screens connected)")
        return
    except Exception:
        pass
    s = socket.socket()
    try:
        s.bind(("0.0.0.0", port))
        report("PASS", f"Port {port} is free (start the server with run_windows.bat)")
    except OSError:
        report("FAIL", f"Port {port} is used by another program", "Close it, or use run_8001.bat.")
    finally:
        s.close()


def check_network() -> None:
    from backend.app import lan_addresses
    ips = [ip for ip in lan_addresses() if not ip.startswith("127.")]
    if ips:
        report("PASS", "Wi-Fi / hotspot address for phones: " + ", ".join(ips))
    else:
        report("FAIL", "No Wi-Fi or hotspot address", "Connect to Wi-Fi or turn on the laptop's mobile hotspot, then rerun.")


def check_power() -> None:
    if platform.system() != "Windows":
        report("PASS", "Power check skipped (not Windows)")
        return
    import ctypes

    class SPS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte), ("BatteryLifePercent", ctypes.c_byte),
                    ("SystemStatusFlag", ctypes.c_byte), ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
    s = SPS()
    if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        if s.ACLineStatus == 1:
            report("PASS", "Laptop is plugged in")
        else:
            report("WARN", f"Laptop is on battery ({s.BatteryLifePercent} %)",
                   "Plug it in: on battery this laptop ran the simulation about 5x slower.")
        if s.SystemStatusFlag == 1:
            report("WARN", "Battery saver is ON", "Turn it off (Settings > System > Power & battery).")
    try:
        out = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, text=True, timeout=5).stdout
        if "saver" in out.lower():
            report("WARN", "Windows power plan is 'Power saver'", "Switch to Balanced or Best performance.")
    except Exception:
        pass


def check_firewall() -> None:
    if platform.system() != "Windows":
        return
    try:
        out = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", "name=all", "dir=in", "verbose"],
                             capture_output=True, text=True, timeout=15).stdout.lower()
    except Exception:
        report("WARN", "Could not read the firewall rules")
        return
    exe = os.path.normcase(sys.executable)
    blocks = out.split("\n\n")
    allowed = any(exe in b and "action:" in b and "allow" in b for b in blocks)
    if allowed:
        report("PASS", "Windows firewall allows this Python (phones can connect)")
    else:
        report("WARN", "No firewall rule found for this Python",
               "When Windows asks on first start, tick 'Private networks' and Allow. Otherwise phones cannot connect.")


def check_accounts() -> None:
    from backend.config import Settings
    cfg = Settings()
    if not cfg.auth:
        report("WARN", "Sign-in is switched OFF (AEGIS_AUTH=0)", "Anyone on the Wi-Fi can control the demo.")
        return
    p = Path(cfg.users_file)
    if not p.exists():
        report("PASS", "Sign-in is on; accounts are created on the first start (printed in the server window)")
        return
    users = json.loads(p.read_text()).get("users", {})
    roles = sorted({u["role"] for u in users.values()})
    report("PASS", f"Sign-in is on: {len(users)} accounts ({', '.join(roles)})")
    if (p.parent / "FIRST_LOGIN.txt").exists():
        report("WARN", "data/FIRST_LOGIN.txt still holds the first passwords",
               "Note them down (or change them: python -m backend.users passwd commander), then delete the file.")


def check_speed() -> None:
    from backend.commands import parse_command
    from backend.config import Settings
    from sim.world import SimArena
    import sim.world as W
    a = SimArena(Settings())
    a.handle(parse_command({"type": "cmd", "action": "load_scenario", "name": "coastal_watch"}))
    a.handle(parse_command({"type": "cmd", "action": "play"}))
    a.handle(parse_command({"type": "cmd", "action": "set_speed", "speed": 10}))
    for _ in range(10):
        a.step(0.05)
    w = next(iter(a.worlds.values()))
    t0, c0, ticks = w.t, time.perf_counter(), 0
    while time.perf_counter() - c0 < 4.0:
        a.step(0.05)
        ticks += 1
        time.sleep(max(0.0, 0.05 * ticks - (time.perf_counter() - c0)))
    rate = (w.t - t0) / (time.perf_counter() - c0)
    msg = f"Simulation speed at 10x on this laptop: {rate:.1f}x"
    if rate >= 7:
        report("PASS", msg)
    elif rate >= 3.5:
        report("WARN", msg, "Plug in and close heavy apps; 4x will still look smooth.")
    else:
        report("WARN", msg, "This laptop is slow right now: present at 1x-2x, or plug in and close other apps.")
    _ = W


def main() -> int:
    print("ABHEDYA pre-flight check\n")
    for f in (check_python, check_builds, check_server, check_network, check_power, check_firewall, check_accounts, check_speed):
        try:
            f()
        except Exception as e:  # a broken check must not hide the others
            report("WARN", f"{f.__name__} could not run", repr(e))
    fails = sum(r[0] == "FAIL" for r in results)
    warns = sum(r[0] == "WARN" for r in results)
    print(f"\n{'READY' if not fails else 'NOT READY'}: {fails} to fix, {warns} warnings.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
