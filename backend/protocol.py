"""Shared constants and the message envelope. Mirrors shared/schema.json."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from sim import __version__ as SERVER_VERSION  # noqa: F401  (re-exported; the single version number lives in sim/__init__.py)

SCHEMA_VERSION = "1.0"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "shared" / "schema.json"

N_CHANNELS = 8
CHANNELS_MHZ = [2410.0 + 10.0 * k for k in range(N_CHANNELS)]  # 2410 .. 2480 MHz
FFT_BINS = 128
BINS_PER_CHANNEL = FFT_BINS // N_CHANNELS  # 16
F_START_MHZ = CHANNELS_MHZ[0] - 5.0
F_STOP_MHZ = CHANNELS_MHZ[-1] + 5.0

ARENA_BOUNDS = {"x": [-300.0, 1200.0], "y": [-400.0, 900.0]}
STUDIO_BOUNDS = {"x": [-200.0, 1400.0], "y": [-400.0, 1000.0]}

JAMMER_TYPES = ["barrage", "random", "sweep", "reactive", "deceptive", "accidental", "follower", "smart", "novel"]
CNN_CLASSES = ["clean", "barrage", "random", "sweep", "reactive", "deceptive", "accidental"]
SECURITY_TESTS = ["legit", "tamper", "spoof", "replay", "eavesdrop", "desync", "impersonate", "capture", "reboot", "clock_loss"]
SCENARIOS = ["border_convoy", "urban_patrol", "coastal_watch", "forest_ambush", "airbase_spoof", "relief_camp", "drone_delivery"]


def envelope(msg_type: str, src: str = "server", **payload: Any) -> dict[str, Any]:
    """Build a server->client message with the standard envelope."""
    msg: dict[str, Any] = {"v": SCHEMA_VERSION, "type": msg_type, "t": round(time.time(), 3), "src": src}
    msg.update(payload)
    return msg


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
