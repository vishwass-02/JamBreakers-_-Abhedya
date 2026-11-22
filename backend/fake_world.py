"""Placeholder world for Phase 0.

Produces schema-valid messages that look and react roughly like the real thing, so the 3D view and
the phone app can be built in parallel with the simulation. Nothing here is real radio physics or AI:
every message carries src='fake_world' and hello says world_source='fake'. The real simulation in sim/
replaces this in Phase 1 through the same World interface.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

from . import commands as C
from .protocol import SERVER_VERSION
from .protocol import (
    ARENA_BOUNDS, BINS_PER_CHANNEL, CHANNELS_MHZ, CNN_CLASSES, F_START_MHZ, F_STOP_MHZ, FFT_BINS,
    JAMMER_TYPES, N_CHANNELS, envelope,
)

SRC = "fake_world"
NOISE_DBM = -100.0
TX_DBM = 30.0

# Which CNN class the fake "AI" reports for each jammer type (follower/smart look like narrowband).
FAKE_CLASS = {"barrage": "barrage", "random": "random", "sweep": "sweep", "reactive": "reactive",
              "deceptive": "deceptive", "accidental": "accidental", "follower": "reactive", "smart": "deceptive"}
DEFAULT_POWER = {"barrage": 36.0, "random": 36.0, "sweep": 38.0, "reactive": 34.0, "deceptive": 30.0,
                 "accidental": 24.0, "follower": 34.0, "smart": 36.0}


def path_loss_db(d: float) -> float:
    """Log-distance path loss at 2.4 GHz (n = 2.7, 1 m reference 40 dB)."""
    return 40.0 + 27.0 * math.log10(max(d, 1.0))


def _dist(a, b) -> float:
    return math.dist(a[:3], b[:3]) if len(a) >= 3 and len(b) >= 3 else math.dist(a[:2], b[:2])


@dataclass
class Jammer:
    id: str
    type: str
    pos: list[float]
    power_dbm: float
    owner: str | None = None
    channels: list[int] = field(default_factory=list)
    phase: float = 0.0


class FakeWorld:
    """One world. The arena wraps one ('main') or two ('ai_off' / 'ai_on') of these."""

    def __init__(self, world_id: str, seed: int, ai_enabled: bool):
        self.world_id = world_id
        self.rng = random.Random(seed)
        self.ai_enabled = ai_enabled
        self.sim_time = 0.0
        self.tick = 0
        self.channel = 0
        self.tx_boost = 0.0
        self.modulation = "QPSK"
        self.jammers: dict[str, Jammer] = {}
        self._jid = 0
        self.hops = 0
        self.latencies: list[float] = []
        self.outage_ms = 0.0
        self.frames_verified = 0
        self.frames_rejected = 0
        self.jammed_since: float | None = None
        self.last_action_t = -10.0
        self.last_decision: dict[str, Any] | None = None
        self.keyed_mode = False
        self.nodes = self._make_nodes()
        self.link_pairs = [("D1", "CP"), ("D2", "CP"), ("D3", "CP"), ("D4", "CP"), ("V1", "CP"), ("V2", "CP"),
                           ("D1", "D2"), ("D3", "D4"), ("V1", "D1"), ("V2", "D3")]

    # ---------- world setup ----------
    def _make_nodes(self) -> dict[str, dict[str, Any]]:
        nodes = {"CP": {"id": "CP", "kind": "command_post", "pos": [0.0, 0.0, 4.0]}}
        for i in range(4):
            nodes[f"D{i+1}"] = {"id": f"D{i+1}", "kind": "drone", "pos": [0.0, 0.0, 100.0],
                                "_r": 250.0 + 120.0 * i, "_w": 0.05 + 0.015 * i, "_p": i * math.pi / 2}
        nodes["V1"] = {"id": "V1", "kind": "vehicle", "pos": [0.0, 0.0, 1.5], "_seg": 0}
        nodes["V2"] = {"id": "V2", "kind": "vehicle", "pos": [0.0, 0.0, 1.5], "_seg": 1}
        self._move(nodes, 0.0)
        return nodes

    def _move(self, nodes: dict[str, dict[str, Any]], t: float) -> None:
        for n in nodes.values():
            if n["kind"] == "drone":
                a = n["_p"] + n["_w"] * t
                n["pos"] = [round(450 + n["_r"] * math.cos(a), 2), round(250 + n["_r"] * 0.7 * math.sin(a), 2),
                            round(90 + 15 * math.sin(a * 2), 2)]
                n["heading_deg"] = round((math.degrees(a) + 90) % 360, 1)
            elif n["kind"] == "vehicle":
                # back and forth along a road
                s = (t * 8.0 + 400 * n["_seg"]) % 1600
                x = s if s <= 800 else 1600 - s
                y = -150 + 300 * n["_seg"] + 40 * math.sin(x / 120)
                n["pos"] = [round(x - 100, 2), round(y, 2), 1.5]
                n["heading_deg"] = 90.0 if s <= 800 else 270.0

    # ---------- commands ----------
    def add_jammer(self, jtype: str, pos: tuple[float, float], power: float | None, owner: str | None) -> Jammer:
        self._jid += 1
        x = min(max(pos[0], ARENA_BOUNDS["x"][0]), ARENA_BOUNDS["x"][1])
        y = min(max(pos[1], ARENA_BOUNDS["y"][0]), ARENA_BOUNDS["y"][1])
        j = Jammer(id=f"J{self._jid}", type=jtype, pos=[x, y, 2.0],
                   power_dbm=power if power is not None else DEFAULT_POWER[jtype], owner=owner)
        self.jammers[j.id] = j
        return j

    def clear_jammers(self) -> None:
        self.jammers.clear()
        self.keyed_mode = False
        self.tx_boost = 0.0
        self.modulation = "QPSK"

    # ---------- radio-ish model ----------
    def _jammer_channels(self, j: Jammer, dt: float) -> list[int]:
        j.phase += dt
        if j.type == "barrage":
            return list(range(N_CHANNELS))
        if j.type == "random":
            if self.tick % 4 == 0 or not j.channels:
                return self.rng.sample(range(N_CHANNELS), 3)
            return j.channels
        if j.type == "sweep":
            return [int(j.phase / 0.15) % N_CHANNELS]
        if j.type == "deceptive":
            return j.channels or [self.channel]  # imitates our traffic on the channel it found first
        if j.type in ("reactive", "follower", "smart"):
            if self.keyed_mode and j.type in ("follower", "smart"):
                # keyed fast hopping: the follower can no longer predict us, it guesses
                if self.tick % 3 == 0 or not j.channels:
                    return [self.rng.randrange(N_CHANNELS)]
                return j.channels
            # re-finds our channel some time after each hop
            delay = 0.3 if j.type == "follower" else 1.5
            if not j.channels or self.sim_time - self.last_action_t >= delay:
                return [self.channel]
            return j.channels
        if j.type == "accidental":
            return j.channels or [self.rng.randrange(N_CHANNELS)]
        return []

    def _interference_dbm(self, rx_pos, channel: int) -> float:
        total_mw = 0.0
        for j in self.jammers.values():
            if channel in j.channels:
                p = j.power_dbm - path_loss_db(_dist(j.pos, rx_pos))
                total_mw += 10 ** (p / 10)
        return 10 * math.log10(total_mw) if total_mw > 0 else -200.0

    def _links(self) -> list[dict[str, Any]]:
        out = []
        noise_mw = 10 ** (NOISE_DBM / 10)
        for a, b in self.link_pairs:
            pa, pb = self.nodes[a]["pos"], self.nodes[b]["pos"]
            sig = TX_DBM + self.tx_boost - path_loss_db(_dist(pa, pb)) + self.rng.gauss(0, 1.0)
            i_dbm = self._interference_dbm(pb, self.channel)
            sinr = sig - 10 * math.log10(noise_mw + 10 ** (i_dbm / 10))
            if self.modulation == "BPSK":
                sinr += 3.0
            status = "ok" if sinr >= 10 else ("degraded" if sinr >= 3 else "down")
            delivery = 100.0 / (1.0 + math.exp(-(sinr - 6.0) / 1.5))
            out.append({"a": a, "b": b, "sinr_db": round(sinr, 1), "status": status, "channel": self.channel,
                        "delivery_pct": round(delivery, 1), "routed": b == "CP"})
        return out

    def _channel_energy(self, rx_pos) -> list[float]:
        return [self._interference_dbm(rx_pos, c) for c in range(N_CHANNELS)]

    # ---------- fake AI ----------
    def _fake_ai(self, links: list[dict[str, Any]]) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = []
        bad = [l for l in links if l["status"] != "ok"]
        if not bad or not self.jammers:
            self.jammed_since = None
            return msgs
        if self.jammed_since is None:
            self.jammed_since = self.sim_time
        if not self.ai_enabled or self.sim_time - self.last_action_t < 0.5:
            return msgs
        if self.sim_time - self.jammed_since < 0.05:
            return msgs  # wait one more tick, like a 3-window vote
        j = max(self.jammers.values(), key=lambda jj: jj.power_dbm - path_loss_db(_dist(jj.pos, self.nodes["CP"]["pos"])))
        cls = FAKE_CLASS[j.type]
        conf = round(self.rng.uniform(0.86, 0.97), 2)
        energy = self._channel_energy(self.nodes["CP"]["pos"])
        frm = self.channel
        latency = round(self.rng.uniform(25, 70), 1)
        if j.type == "accidental":
            action, to, text = "ignore", frm, "Interference looks accidental (friendly emitter). No action needed."
        elif j.type == "sweep":
            self.tx_boost = min(self.tx_boost + 3.0, 9.0)
            action, to = "raise_power", frm
            text = f"Sweep jammer crosses every channel, so hopping won't help. Raised power to +{self.tx_boost:.0f} dB and holding."
        elif j.type == "barrage":
            self.tx_boost = min(self.tx_boost + 3.0, 9.0)
            self.modulation = "BPSK"
            action, to = "lower_rate", frm
            text = "Barrage covers the whole band. Raised power and dropped to BPSK to keep the link alive."
        else:
            candidates = [c for c in range(N_CHANNELS) if c != frm]
            to = min(candidates, key=lambda c: energy[c] + self.rng.random() * 0.01)
            action = "fast_hop" if j.type in ("follower", "smart") else "hop"
            if action == "fast_hop":
                self.keyed_mode = True
            self.channel = to
            self.hops += 1
            self.latencies.append(latency)
            text = (f"Follower jammer keeps landing on our channel. Switched to keyed fast hopping, now on channel {to}."
                    if action == "fast_hop" else f"CNN sees {cls} jamming ({conf:.0%}). Hopped from channel {frm} to {to} in {latency:.0f} ms.")
        self.last_action_t = self.sim_time
        self.last_decision = {"class": cls, "conf": conf}
        msgs.append(envelope("decision", SRC, world=self.world_id, sim_time_s=round(self.sim_time, 3), **{"class": cls},
                             confidence=conf, action=action, from_channel=frm, to_channel=to,
                             latency_ms=latency if action in ("hop", "fast_hop") else None,
                             node_ids=sorted(self.nodes), reason=text))
        msgs.append(envelope("event", SRC, world=self.world_id, level="good" if action != "ignore" else "info", text=text))
        return msgs

    # ---------- spectrum ----------
    def _spectrum(self) -> dict[str, Any]:
        rx = self.nodes["CP"]["pos"]
        bins = []
        for k in range(FFT_BINS):
            ch, off = divmod(k, BINS_PER_CHANNEL)
            shape = math.exp(-((off - 7.5) / 4.5) ** 2)  # channel-shaped bump
            p_mw = 10 ** ((NOISE_DBM + self.rng.gauss(0, 1.5)) / 10)
            if ch == self.channel:
                p_mw += 10 ** ((-62 + self.tx_boost) / 10) * shape
            for j in self.jammers.values():
                if ch in j.channels:
                    p = j.power_dbm - path_loss_db(_dist(j.pos, rx))
                    width = 1.0 if j.type in ("barrage", "random") else shape
                    p_mw += 10 ** (p / 10) * max(width, 0.05)
            bins.append(round(10 * math.log10(p_mw), 1))
        jammed = bool(self.jammers) and any(self.channel in j.channels or j.type == "barrage" for j in self.jammers.values())
        if self.jammers and self.last_decision:
            verdict, conf = self.last_decision["class"], self.last_decision["conf"]
        elif self.jammers:
            j = next(iter(self.jammers.values()))
            verdict, conf = FAKE_CLASS[j.type], 0.6
        else:
            verdict, conf = "clean", 0.97
        rest = (1.0 - conf) / (len(CNN_CLASSES) - 1)
        probs = {c: round(conf if c == verdict else rest, 4) for c in CNN_CLASSES}
        msg = envelope("spectrum", SRC, world=self.world_id, node_id="CP", f_start_mhz=F_START_MHZ,
                       f_stop_mhz=F_STOP_MHZ, bins=bins, anomaly=jammed, probs=probs, verdict=verdict,
                       confidence=round(conf, 4))
        if self.jammers and self.tick % 10 == 0:
            msg["gradcam"] = self._gradcam()
        return msg

    def _gradcam(self) -> list[list[float]]:
        hot = set()
        for j in self.jammers.values():
            for ch in j.channels:
                hot.update(range(ch * 4, ch * 4 + 4))  # 32 columns = 4 per channel
        return [[round(min(1.0, (0.85 if c in hot else 0.05) + self.rng.uniform(0, 0.12)), 2) for c in range(32)]
                for _ in range(12)]

    # ---------- main step ----------
    def step(self, dt: float) -> list[dict[str, Any]]:
        self.sim_time += dt
        self.tick += 1
        self._move(self.nodes, self.sim_time)
        for j in self.jammers.values():
            j.channels = self._jammer_channels(j, dt)
        links = self._links()
        msgs = self._fake_ai(links)
        if msgs:
            links = self._links()  # show the effect of the decision immediately
        routed = [l for l in links if l["routed"]]
        delivery = sum(l["delivery_pct"] for l in routed) / len(routed)
        if any(l["status"] == "down" for l in routed):
            self.outage_ms += dt * 1000
        self.frames_verified += int(len(routed) * 5 * delivery / 100)  # 5 radio slots per tick
        lat = sorted(self.latencies)
        kpis = {
            "delivery_pct": round(delivery, 1),
            "hop_latency_ms": lat[len(lat) // 2] if lat else None,
            "hop_latency_p95_ms": lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else None,
            "outage_ms": round(self.outage_ms, 1),
            "healthy_links": sum(1 for l in links if l["status"] == "ok"),
            "total_links": len(links),
            "hops": self.hops,
            "decision_time_ms": 4.2 if self.last_decision else None,
            "frames_verified": self.frames_verified,
            "frames_rejected": self.frames_rejected,
            "frames_forged": 0,
        }
        node_status = {nid: "ok" for nid in self.nodes}
        for l in links:
            if l["routed"]:
                node_status[l["a"]] = l["status"]
        nodes = [{"id": n["id"], "kind": n["kind"], "pos": n["pos"], "heading_deg": n.get("heading_deg", 0.0),
                  "channel": self.channel, "tx_power_dbm": TX_DBM + self.tx_boost, "modulation": self.modulation,
                  "status": node_status[n["id"]]} for n in self.nodes.values()]
        jammers = [{"id": j.id, "type": j.type, "pos": j.pos, "power_dbm": j.power_dbm, "channels": j.channels,
                    "active": True, **({"owner": j.owner} if j.owner else {})} for j in self.jammers.values()]
        estimates = []
        if self.ai_enabled:
            for j in self.jammers.values():
                if j.type == "accidental":
                    continue
                r = max(40.0, 160.0 - 20.0 * self.sim_time % 120)
                estimates.append({"jammer_id": j.id, "pos": [round(j.pos[0] + self.rng.gauss(0, 15), 1),
                                                             round(j.pos[1] + self.rng.gauss(0, 15), 1)],
                                  "radius_m": round(r, 1)})
        state = envelope("state", SRC, world=self.world_id, tick=self.tick, sim_time_s=round(self.sim_time, 3),
                         ai_enabled=self.ai_enabled, view="single", mode="arena", scenario=None, paused=False, speed=1,
                         nodes=nodes, links=links, jammers=jammers, estimates=estimates, obstacles=[], kpis=kpis)
        return [state, self._spectrum(), *msgs]


class FakeArena:
    """World implementation used by the server in Phase 0."""

    def __init__(self, seed: int = 42):
        self.seed = seed
        self.rng = random.Random(seed)
        self.view = "single"
        self.ai_enabled = True
        self.paused = False
        self.speed = 1
        self.worlds: dict[str, FakeWorld] = {}
        self.leaderboard: dict[str, int] = {}
        self._bench: dict[str, Any] | None = None
        self._jammer_log: list[tuple[str, tuple[float, float], float | None, str | None]] = []
        self._build()

    def _build(self) -> None:
        if self.view == "single":
            self.worlds = {"main": FakeWorld("main", self.seed, self.ai_enabled)}
        else:
            self.worlds = {"ai_off": FakeWorld("ai_off", self.seed, False), "ai_on": FakeWorld("ai_on", self.seed, True)}
        for args in self._jammer_log:
            for w in self.worlds.values():
                w.add_jammer(*args)

    # ---- World protocol ----
    def hello_info(self) -> dict[str, Any]:
        return {"server_version": SERVER_VERSION, "schema_version": "1.0", "world_source": "fake", "signal_backend": "none",
                "channels_mhz": CHANNELS_MHZ, "fft_bins": FFT_BINS, "bounds": ARENA_BOUNDS,
                "jammer_types": JAMMER_TYPES, "classes": CNN_CLASSES, "ai_enabled": self.ai_enabled,
                "view": self.view, "key_fingerprint": "FAKE-00:00:00:00"}

    def step(self, dt: float) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not self.paused:
            for w in self.worlds.values():
                for m in w.step(dt * self.speed):
                    if m["type"] == "state":
                        m.update(view=self.view, paused=self.paused, speed=self.speed)
                    out.append(m)
        else:
            # still send state so screens show 'paused' and stay alive
            for w in self.worlds.values():
                m = w.step(0.0)[0]
                m.update(view=self.view, paused=True, speed=self.speed)
                out.append(m)
        out.extend(self._bench_step())
        return out

    def handle(self, cmd: C.Command) -> list[dict[str, Any]]:
        rid = {"req_id": cmd.id} if cmd.id else {}
        if isinstance(cmd, C.LaunchJammer):
            args = (cmd.jammer_type, cmd.pos, cmd.power_dbm, cmd.player)
            self._jammer_log.append(args)
            j = None
            for w in self.worlds.values():
                j = w.add_jammer(*args)
            who = cmd.player or "Someone"
            out = [envelope("event", SRC, level="alert", text=f"{who} launched a {cmd.jammer_type} jammer ({j.id}).", **rid)]
            if cmd.player:
                self.leaderboard[cmd.player] = self.leaderboard.get(cmd.player, 0) + 1
                out.append(envelope("score", SRC, leaderboard=[{"player": p, "attacks": n, "damage": 0.0}
                                                                for p, n in sorted(self.leaderboard.items(), key=lambda kv: -kv[1])]))
            return out
        if isinstance(cmd, C.ClearJammers):
            self._jammer_log.clear()
            for w in self.worlds.values():
                w.clear_jammers()
            return [envelope("event", SRC, level="good", text="All jammers cleared.", **rid)]
        if isinstance(cmd, C.ToggleAI):
            self.ai_enabled = (not self.ai_enabled) if cmd.enabled is None else cmd.enabled
            if "main" in self.worlds:
                self.worlds["main"].ai_enabled = self.ai_enabled
            return [envelope("event", SRC, level="info", text=f"AI defender {'ON' if self.ai_enabled else 'OFF'}.", **rid)]
        if isinstance(cmd, C.SetView):
            self.view = cmd.view
            self._build()
            text = "Side-by-side: AI OFF (left) vs AI ON (right), same attack." if cmd.view == "side_by_side" else "Single view."
            return [envelope("event", SRC, level="info", text=text, **rid)]
        if isinstance(cmd, C.SecurityTest):
            return self._security(cmd.test, rid)
        if isinstance(cmd, C.RunBenchmark):
            if self._bench is not None:
                return [envelope("error", SRC, code="busy", message="A benchmark is already running.", **rid)]
            self._bench = {"ticks": 0, "seed": cmd.seed if cmd.seed is not None else self.seed}
            return [envelope("event", SRC, level="info", text="Benchmark started (placeholder numbers).", **rid)]
        if isinstance(cmd, C.Pause):
            self.paused = True
            return [envelope("event", SRC, level="info", text="Paused.", **rid)]
        if isinstance(cmd, C.Play):
            self.paused = False
            return [envelope("event", SRC, level="info", text="Playing.", **rid)]
        if isinstance(cmd, C.SetSpeed):
            self.speed = cmd.speed
            return [envelope("event", SRC, level="info", text=f"Speed {cmd.speed}x.", **rid)]
        if isinstance(cmd, C.Restart):
            self._jammer_log.clear()
            self.paused = False
            self._build()
            return [envelope("event", SRC, level="info", text="Restarted.", **rid)]
        # load_scenario / exit_scenario arrive in Phase 6
        return [envelope("error", SRC, code="not_implemented", message=f"'{cmd.action}' is not built yet.", **rid)]

    # ---- helpers ----
    def _security(self, test: str, rid: dict[str, str]) -> list[dict[str, Any]]:
        header = bytes(self.rng.randrange(256) for _ in range(10)).hex()
        ct = bytes(self.rng.randrange(256) for _ in range(48)).hex()
        reasons = {
            "tamper": "Rejected: GCM authentication tag mismatch (1 bit flipped). [placeholder]",
            "spoof": "Rejected: sender not keyed with the mission key, tag invalid. [placeholder]",
            "replay": "Rejected: sequence number already seen inside the 64-frame replay window. [placeholder]",
            "eavesdrop": "Attacker sees only ciphertext; nothing learnt without the key. [placeholder]",
            "legit": "Genuine frame verified. [placeholder]",
        }
        for w in self.worlds.values():
            if test != "eavesdrop":
                w.frames_rejected += 1
        msg = envelope("security", SRC, test=test, accepted=test == "legit", reason=reasons[test], header_hex=header,
                       ciphertext_hex=ct, **rid)
        if test == "eavesdrop":
            msg["plaintext_hint"] = "unreadable"
        return [msg, envelope("event", SRC, level="good", text=f"Security test '{test}': {reasons[test]}", **rid)]

    def _bench_step(self) -> list[dict[str, Any]]:
        if self._bench is None:
            return []
        b = self._bench
        b["ticks"] += 1
        total = 40  # 2 s of fake progress
        if b["ticks"] % 8 and b["ticks"] < total:
            return []
        if b["ticks"] < total:
            return [envelope("benchmark", SRC, status="running", progress=round(b["ticks"] / total, 2), seed=b["seed"],
                             placeholder=True)]
        rows = []
        for jt in ["barrage", "random", "sweep", "reactive", "deceptive", "follower"]:
            rows.append({"jammer": jt, "defender": "fixed_hopping", "delivery_pct": round(self.rng.uniform(35, 65), 1),
                         "hop_latency_median_ms": None, "hop_latency_p95_ms": None,
                         "outage_ms": round(self.rng.uniform(4000, 12000)), "accuracy_pct": None})
            rows.append({"jammer": jt, "defender": "aegis", "delivery_pct": round(self.rng.uniform(82, 96), 1),
                         "hop_latency_median_ms": round(self.rng.uniform(30, 60), 1),
                         "hop_latency_p95_ms": round(self.rng.uniform(60, 95), 1),
                         "outage_ms": round(self.rng.uniform(100, 900)), "accuracy_pct": round(self.rng.uniform(90, 97), 1)})
        self._bench = None
        return [envelope("benchmark", SRC, status="done", progress=1.0, seed=b["seed"], rows=rows, files=[], placeholder=True)]
