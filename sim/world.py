"""The simulated world: radio slots every 10 ms, broadcast every 50 ms.

SimWorld is one world (one network, one set of jammers, one defender). SimArena implements the
server's World interface and holds one world ('main') or two twins ('ai_off' and 'ai_on') that
share a seed and an attack timeline, so the side-by-side view shows the same attack twice.

Determinism: every source of randomness has its own stream from one SeedSequence (fading,
packets, jammers, control frames, spectrum, defender). The same seed and the same commands at the
same slots give the same run, and the twins see identical fading and jammer randomness.
"""
from __future__ import annotations

import hashlib
import logging
import os
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
from cryptography.exceptions import InvalidSignature, InvalidTag

from backend import commands as C
from backend.protocol import ARENA_BOUNDS, CHANNELS_MHZ, CNN_CLASSES, FFT_BINS, F_START_MHZ, F_STOP_MHZ, JAMMER_TYPES, envelope

from .defenders import Decision, Defender, SlotObs, make_defender
from .safety import SafetyGuard
from . import battery as bat
from .keymgmt import KeyManager, SessionReceiver
from .missionlog import MissionLog, label_for
from .qos import QoSLayer
from .timesync import GUARD_S, TimeSync
from .hopping import fixed_channel, keyed_channel, rendezvous_channel
from .jammers import Jammer, SlotCtx, make_jammer
from . import __version__
from .network import ARENA_OBSTACLES, LIVE_TRUCK_SPEEDS, Network, Node, arena_nodes
from .phy import EmitterSpec, Phy, get_phy
from . import security as sec
from . import scenarios as scn
from .radio import (
    N_CH, NOISE_INBAND_DBM, POWER_CAP_DB, SLOT_S, TX_POWER_DBM, channel_offset_hz,
    dbm_to_mw, mw_to_dbm,
)

log = logging.getLogger("aegis.sim")
SRC = "sim"
SLOTS_PER_TICK = 5
KEY_EPOCH_S = 60.0
MAX_BLACKLIST = 4
CONTROL_MARGIN_DB = 12.0         # control frames: max power, BPSK, 4x repetition
RESYNC_EVERY = 5                 # slots between resync beacons on the rendezvous channel (50 ms)
LOST_DETECT_SLOTS = 3            # a node notices it is out of sync after hearing nothing for 30 ms
STREAMS = ["fade", "pkt", "jam", "ctrl", "phy", "def"]
MAX_JAMMERS = 12                       # user-launched jammers on the air at once (oldest switched off first)
TICK_BUDGET_S = 0.035                  # CPU per 50 ms tick before simulated time is slowed (leaves room for I/O)
TICK_BUDGET_10X_S = 0.042              # at 10x the spectrum goes out 5x a second instead of 20, which frees this much more
AI_ON_DEFENDER = "hybrid"        # AEGIS (CNN + rules) leads, the RL policy takes over when it does better (Phase 8)
AI_OFF_DEFENDER = "fixed_hopping"
AI_DEFENDERS = ("aegis", "rl", "hybrid")   # decisions made by an AI go through the safety guard


def fingerprint(key: bytes) -> str:
    return sec.fingerprint(key)


NODE_ADDR = {"CP": 1, "D1": 2, "D2": 3, "D3": 4, "D4": 5, "V1": 6, "V2": 7, "R1": 8}
ADDR_NODE = {v: k for k, v in NODE_ADDR.items()}


@dataclass
class Pending:
    decision: Decision
    created_slot: int
    next_try: int
    attempts: int = 0


class SimWorld:
    def __init__(self, world_id: str, seed: int, defender: str, phy: Phy, spectrum_every: int = 1, mission: int = 1,
                 scenario: Optional[dict] = None, live: bool = False):
        self.world_id = world_id
        self.seed = seed
        self.phy = phy
        self.spectrum_every = spectrum_every
        ss = np.random.SeedSequence(seed)
        self.rng = {name: np.random.default_rng(s) for name, s in zip(STREAMS, ss.spawn(len(STREAMS)))}
        self.mission = mission
        # per-radio device keys + a rekeyable group key + signed orders (sim/keymgmt.py); every radio JOINs at start
        self.km = KeyManager(seed, mission, {n: a for n, a in NODE_ADDR.items() if n != "CP"})
        self.radios = {n: self.km.provision(n, a) for n, a in NODE_ADDR.items() if n != "CP"}
        for r in self.radios.values():
            self._join(r)
        self.rx = SessionReceiver(self.km)
        self.revoked_nodes: set[str] = set()
        self.clock: Optional[TimeSync] = None                 # network time (sim/timesync.py), made once the nodes exist
        self.qos = QoSLayer("qos", seed)                       # message priorities and store-and-forward (sim/qos.py)
        self.seq: dict[str, int] = {}
        self.seq_epoch = 0
        self.heard: deque[bytes] = deque(maxlen=64)            # frames an eavesdropper could have recorded
        self.attacker_key = sec.mission_key(seed + 99991, mission)  # a key that is not ours
        self.scenario = scenario
        if scenario:
            self.net = Network(scn.build_nodes(scenario), scn.build_obstacles(scenario))
            bat.init(self.net.nodes, arena=False)
            self.schedule = [{"spec": j, "id": f"S{k + 1}", "on": int(j.get("on_s", 0) / SLOT_S),
                              "off": int(j["off_s"] / SLOT_S) if j.get("off_s") is not None else None, "live": False}
                             for k, j in enumerate(scenario.get("jammers", []))]
        else:
            self.net = Network(arena_nodes(LIVE_TRUCK_SPEEDS) if live else arena_nodes(), [dict(o) for o in ARENA_OBSTACLES])
            bat.init(self.net.nodes, arena=True)
            self.schedule = []
        self.jammers: list[Jammer] = []
        self._remove_ids: set[str] = set()
        self._started: set[str] = set()
        self._launch_queue: list[Jammer] = []
        self._clear_pending = False
        self.slot = 0
        self.tick = 0
        self.outbox: list[dict] = []
        self.last_window: Optional[np.ndarray] = None
        self.last_bins: Optional[list[float]] = None
        self.set_defender(defender)
        self.channel = self.channel_for_slot(0)
        self._reset_metrics()
        self.net.update_geometry(0.0, self.jammers)
        self.net.route()

    # ---------- setup ----------
    def set_defender(self, name: str) -> None:
        self.defender: Defender = make_defender(name, self.rng["def"])
        self.defender_name = self.defender.name
        # every AI decision is checked before it is sent, and undone if it made things worse (sim/safety.py)
        self.guard: Optional[SafetyGuard] = SafetyGuard(POWER_CAP_DB) if self.defender_name in AI_DEFENDERS else None
        self.slot_hist: deque[tuple[int, int]] = deque(maxlen=50)      # (sent, delivered) per slot, last 0.5 s
        self.hop_mode = self.defender.initial_mode
        self.dwell_channel = 0
        self.blacklist: dict[int, float] = {}         # channel -> expiry time
        self.recent_channels: deque[int] = deque(maxlen=3)
        self.anti_follow = False
        self.pending: Optional[Pending] = None
        self.net.tx_boost_db = getattr(self.defender, "initial_boost_db", 0.0)
        self.net.modulation = getattr(self.defender, "initial_modulation", "QPSK")
        self.boost_slots = 0.0           # sum of tx boost (dB) over slots: how much extra power the defence spends
        had_relay = any(n.kind == "relay" for n in self.net.nodes)
        self.net.remove_relays()
        if had_relay and hasattr(self.net, "positions"):
            self.net.update_geometry(self.t, self.jammers)
            self.net.route()
        self.ai_enabled = name not in ("fixed_hopping", "none")

    def _reset_metrics(self) -> None:
        self.recent: deque[tuple[int, int]] = deque(maxlen=100)     # (sent, delivered) per slot, last 1 s
        self.attack_sent = self.attack_delivered = 0                  # only while a jammer is on the air
        self.loss5: deque[tuple[int, int]] = deque(maxlen=5)
        self.total_sent = self.total_delivered = 0
        self.outage_ms = 0.0
        self.latencies: list[float] = []
        self.hops = 0
        self.frames_forged = 0
        self.frames_rejected = 0
        self.frames_forged_accepted = 0
        self.auth_syncs = 0
        self.lost_nodes: set[str] = set()
        self.desynced: dict[str, int] = {}     # node -> slot it missed a control frame (it is on the old settings)
        self.resync_ms: list[float] = []       # how long each out-of-sync node took to find the network again
        self.decision_time_ms: Optional[float] = None
        self.jam_start: Optional[int] = None
        self.resp_start: Optional[int] = None      # first jammed slot not yet answered by any countermeasure
        self.response_latencies: list[float] = []
        self.node_ema = {n.id: 1.0 for n in self.net.nodes}

    @property
    def t(self) -> float:
        return self.slot * SLOT_S

    def launch(self, jammer: Jammer) -> None:
        self._launch_queue.append(jammer)

    def clear_jammers(self) -> None:
        self._clear_pending = True

    def remove_jammer(self, jid: str) -> None:
        self._remove_ids.add(jid)

    # ---------- channel plan ----------
    def active_blacklist(self) -> frozenset:
        return frozenset(c for c, exp in self.blacklist.items() if exp > self.t)

    def channel_for_slot(self, slot: int) -> int:
        if self.hop_mode == "fixed":
            return fixed_channel(slot)
        if self.hop_mode == "keyed":
            # never the last 3 channels (a follower re-tunes ~25 ms late) nor right next to the last one
            if not self.anti_follow:
                return keyed_channel(self.hop_key(slot), slot, self.active_blacklist())
            soft = {(self.recent_channels[-1] + d) % N_CH for d in (-1, 1)} if self.recent_channels else set()
            return keyed_channel(self.hop_key(slot), slot, self.active_blacklist(), set(self.recent_channels), soft)
        return self.dwell_channel

    @property
    def ks(self) -> sec.KeySchedule:
        """Group key schedule: hop sequence and rendezvous channel (replaced when a radio is revoked)."""
        return self.km.group

    @property
    def key(self) -> bytes:
        return self.km.group_key

    def _join(self, radio) -> str:
        challenge = hashlib.sha256(f"{self.seed}|{radio.name}|{self.km.joins.get(radio.name, 0)}".encode()).digest()[:16]
        grant, why = self.km.handle_join(radio.join_request(challenge))
        return radio.accept_grant(grant) if grant else why

    def seal_from(self, nid: str, payload: bytes, epoch: Optional[int] = None) -> bytes:
        """A data frame from radio nid, sealed with that radio's own session key."""
        src = NODE_ADDR.get(nid, 9)
        return sec.seal(self.radios[nid].session, sec.T_DATA, src, NODE_ADDR["CP"], self.epoch if epoch is None else epoch,
                        self.next_seq(nid), payload)

    def control_frame(self, payload: bytes) -> tuple[bytes, bytes]:
        """An order from the command post: sealed with the group key and signed with the CP's Ed25519 key."""
        frame = sec.seal(self.km.group, sec.T_CONTROL, NODE_ADDR["CP"], sec.BROADCAST, self.epoch, self.next_seq("CP"), payload)
        return frame, self.km.sign(frame)

    def verify_control(self, frame: bytes, sig: bytes) -> tuple[bool, str]:
        """What every radio does with an order: check the CP signature, then decrypt with the group key."""
        try:
            self.km.public.verify(sig, frame)
        except InvalidSignature:
            return False, "order not signed by the command post"
        hdr = sec.HEADER.unpack(frame[:sec.HEADER_LEN])
        try:
            self.km.group.aead(self.epoch).decrypt(sec.nonce(hdr[2], self.epoch, hdr[5]), frame[sec.HEADER_LEN:], frame[:sec.HEADER_LEN])
        except InvalidTag:
            return False, "order does not decrypt with the current group key"
        return True, "verified"

    @property
    def epoch(self) -> int:
        return self.ks.epoch_of(self.t)

    def hop_key(self, slot: int) -> bytes:
        """HMAC key of the hop sequence for the epoch this slot is in (HKDF from the mission key)."""
        return self.ks.key("hop", self.ks.epoch_of(slot * SLOT_S))

    def next_seq(self, nid: str) -> int:
        if self.epoch != self.seq_epoch:                    # new epoch = new key: sequence numbers restart
            self.seq_epoch = self.epoch
            self.seq.clear()
        self.seq[nid] = self.seq.get(nid, -1) + 1
        return self.seq[nid]

    def rendezvous(self, attempt: int = 0) -> int:
        """Key-derived control channel. Each retry moves to the next channel of a key-derived
        sequence, so a jammer parked on one channel cannot block the control link."""
        ch = rendezvous_channel(self.ks.key("rdv", self.epoch), attempt)
        bl = self.active_blacklist()
        while ch in bl and len(bl) < N_CH:
            ch = (ch + 1) % N_CH
        return ch

    # ---------- sensing ----------
    def sense(self, node_id: str = "CP") -> np.ndarray:
        """Spectrogram window (12 x 128 dBm) as the receiver at node_id would capture it right now."""
        net = self.net
        ni = net.index[node_id]
        ch = self.channel
        emitters: list[EmitterSpec] = []
        # our own traffic: the neighbour transmitting to this node in this TDMA slot
        clean_snr = TX_POWER_DBM - net.link_loss_db - NOISE_INBAND_DBM
        neighbours = [k for k, (i, j) in enumerate(net.pairs) if ni in (i, j) and clean_snr[k] > 10]
        if neighbours:
            k = neighbours[self.slot % len(neighbours)]
            rx = TX_POWER_DBM + net.tx_boost_db - net.link_loss_db[k]
            emitters.append(EmitterSpec("qpsk", float(rx), channel_offset_hz(ch), k_db=float(net.link_k_db[k]), label="own"))
        for a, jm in enumerate(self.jammers):
            if a < net.jam_gain_db.shape[0]:
                emitters.extend(jm.emitters(float(jm.power_dbm + net.jam_gain_db[a, ni]), self.t, self.rng["phy"]))
        return self.phy.synthesize(emitters, self.rng["phy"], t0=self.t)

    def rssi_reports(self, channel: Optional[int] = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """What each node's wideband receiver measures from hostile emitters: total received power over
        the 80 MHz band, averaged over 8 readings (fading), with 1 dB meter noise. Returns node
        positions, readings (dBm) and each node's path-loss exponent (the system knows which of its own
        nodes are airborne). Readings near the noise floor carry no information and are dropped."""
        from .network import AIRBORNE
        from .radio import NOISE_BAND_DBM, PL_EXPONENT_AIR, PL_EXPONENT_GROUND, RICIAN_K_DB_GROUND, rician_gain_db
        net, rng = self.net, self.rng["def"]
        n = len(net.nodes)
        total = np.zeros(n)
        for a, jm in enumerate(self.jammers):
            if jm.friendly:
                continue
            fades = rician_gain_db(rng, RICIAN_K_DB_GROUND, (8, n))
            total += dbm_to_mw(jm.power_dbm + net.jam_gain_db[a][None, :] + fades).mean(axis=0)
        meas = mw_to_dbm(total + dbm_to_mw(NOISE_BAND_DBM)) + rng.normal(0, 1.0, n)
        exps = np.array([PL_EXPONENT_AIR if nd.kind in AIRBORNE else PL_EXPONENT_GROUND for nd in net.nodes])
        keep = meas > NOISE_BAND_DBM + 10
        return net.positions[keep], meas[keep], exps[keep]

    def nearest_jammer_id(self, x: float, y: float) -> Optional[str]:
        """Ground truth, used only to tag an estimate for scoring and display."""
        if not self.jammers:
            return None
        return min(self.jammers, key=lambda j: (j.pos[0] - x) ** 2 + (j.pos[1] - y) ** 2).id

    # ---------- one radio slot ----------
    def step_slot(self) -> None:
        net = self.net
        if self.slot % SLOTS_PER_TICK == 0:
            self._run_schedule()
            self._apply_jammer_changes()
            bat.update(self, SLOTS_PER_TICK * SLOT_S)
            self._clock_tick(SLOTS_PER_TICK * SLOT_S)
            for jm in self.jammers:
                if getattr(jm, "route", None) is not None:      # jammer on a moving platform (hostile drone, ship)
                    p, _ = jm.route(self.t)
                    jm.pos = [p[0], p[1], p[2]]
            net.update_geometry(self.t, self.jammers)
            net.route()
        ch = self.channel_for_slot(self.slot)
        self.channel = ch
        ctx = SlotCtx(self.slot, self.t, ch, self.rng["jam"])
        for jm in self.jammers:
            if jm.id not in self._started:
                jm.start(ctx)
                self._started.add(jm.id)
            jm.update(ctx)

        sinr, ok, i_mw = net.slot(self.jammers, ch, self.rng["fade"], self.rng["pkt"])
        sent = delivered = 0
        for nd in net.nodes:
            if nd.id == "CP" or nd.kind == "relay" or bat.away(nd) or nd.id in self.revoked_nodes:
                continue
            path = net.path_links(nd.id)
            success = bool(path) and bool(np.all(ok[path])) and nd.id not in self.desynced
            sent += 1
            if success:
                # every packet is a real AES-256-GCM frame, sealed at the node and verified at the command post
                src = NODE_ADDR.get(nd.id, 9)
                frame = self.seal_from(nd.id, sec.telemetry(src, self.t, nd.pos, 0))
                res = self.rx.open(frame, self.epoch)
                success = res.ok
                if res.ok:
                    if self.slot % 7 == 0:
                        self.heard.append(frame)
                else:
                    self.frames_rejected += 1
            delivered += success
            self.node_ema[nd.id] = 0.85 * self.node_ema.get(nd.id, 1.0) + 0.15 * success
        usable = {n.id for n in net.nodes if n.id != "CP" and n.kind != "relay" and not bat.away(n)
                  and n.id not in self.desynced and n.id not in self.revoked_nodes}
        self.qos.slot(self.t, [n.id for n in net.nodes if n.id != "CP" and n.kind != "relay"], net.path_links, ok, usable)
        self.recent.append((sent, delivered))
        self.loss5.append((sent, delivered))
        self.slot_hist.append((sent, delivered))
        self.total_sent += sent
        self.total_delivered += delivered
        if self.jammers:
            self.attack_sent += sent
            self.attack_delivered += delivered
        if sent and delivered / sent < 0.5:
            self.outage_ms += SLOT_S * 1000

        # forged look-alike frames reaching the command post: half spoofed with a wrong key, half replays
        # of frames recorded earlier. Each one goes through the real receiver checks.
        for a, jm in enumerate(self.jammers):
            if jm.forging and ch in jm.channels and jm.power_dbm + net.jam_gain_db[a, net.cp] > NOISE_INBAND_DBM + 6:
                self.frames_forged += 1
                if self.heard and self.rng["jam"].random() < 0.5:
                    fake = self.heard[int(self.rng["jam"].integers(len(self.heard)))]
                else:
                    victim = NODE_ADDR["D2"]
                    fake = sec.seal(sec.KeySchedule(self.attacker_key), sec.T_DATA, victim, NODE_ADDR["CP"], self.epoch,
                                    self.seq.get("D2", 0) + 1, sec.telemetry(victim, self.t, (0, 0, 0), 9))
                res = self.rx.open(fake, self.epoch)
                if res.ok:
                    self.frames_forged_accepted += 1          # must stay 0; tested
                else:
                    self.frames_rejected += 1

        self._resync()
        self._track_lost_nodes()

        # jamming episode: hostile energy on our channel at a node on a route, and packets lost
        noise_mw = dbm_to_mw(NOISE_INBAND_DBM)
        hostile = any(not jm.friendly and jm.frac[ch] > 0 for jm in self.jammers)
        jammed = hostile and delivered < sent and float(np.max(i_mw)) > 2 * noise_mw
        if jammed:
            self._clear_slots = 0
            if self.jam_start is None:
                self.jam_start = self.slot
            if self.resp_start is None:
                self.resp_start = self.slot
        elif self.jam_start is not None and self.pending is None:
            # 3 clean slots in a row without a hop of ours: the episode ended on its own
            self._clear_slots = getattr(self, "_clear_slots", 0) + 1
            if self._clear_slots >= 3:
                self.jam_start = None
                self.resp_start = None

        applied = self._control_plane()
        if self.pending is None and not applied:
            obs = self._observe(sent, delivered)
            t0 = time.perf_counter()
            decision = self.defender.on_slot(obs)
            if self.guard is not None:
                undo = self.guard.maybe_undo(self, self.recent_delivery())
                if undo is not None:
                    decision = undo
                elif decision is not None:
                    decision = self.guard.check(decision, self)
            if decision is not None:
                decision.decision_time_ms = decision.decision_time_ms or round((time.perf_counter() - t0) * 1000, 2)
                self.decision_time_ms = decision.decision_time_ms
                if decision.actions:
                    self.pending = Pending(decision, self.slot, self.slot + 1)
                else:
                    self._emit_decision(decision, latency=None)
            for level, text in getattr(self.defender, "events", []):
                self.outbox.append(envelope("event", SRC, world=self.world_id, level=level, text=text))
            if getattr(self.defender, "events", None):
                self.defender.events.clear()
        self.recent_channels.append(ch)
        self.slot += 1

    def event(self, level: str, text: str) -> None:
        self.outbox.append(envelope("event", SRC, world=self.world_id, level=level, text=text))

    def recent_delivery(self) -> float:
        s = sum(x for x, _ in self.slot_hist)
        return sum(y for _, y in self.slot_hist) / s if s else 1.0

    def _observe(self, sent: int, delivered: int) -> SlotObs:
        s = sum(x for x, _ in self.loss5)
        d = sum(y for _, y in self.loss5)
        energy = self.net.fused_energy_dbm(self.jammers) + self.rng["def"].normal(0, 1.0, N_CH)
        return SlotObs(self.slot, self.t, self.channel, self.hop_mode, sent, delivered, 1 - d / s if s else 0.0,
                       energy, self.net.tx_boost_db, self.net.modulation, self.pending is not None,
                       self.active_blacklist(), self.sense, self)

    # ---------- control plane: decisions take effect only when their frame arrives ----------
    def _control_plane(self) -> None:
        p = self.pending
        if p is None or self.slot < p.next_try:
            return False
        rdv = self.rendezvous(p.attempts)
        ok = self.net.probe(self.jammers, rdv, self.rng["ctrl"], extra_db=CONTROL_MARGIN_DB)
        reached, missed = [], []
        for nd in self.net.nodes:
            if nd.id == "CP":
                continue
            path = self.net.path_links(nd.id)
            got = bool(path) and bool(np.all(ok[path])) and nd.id not in self.desynced
            reached.append(got)
            if not got and nd.id not in self.desynced:
                missed.append(nd.id)
        if reached and np.mean(reached) >= 0.8:
            # the decision travels as an authenticated control frame; it only takes effect if it verifies
            frame, sig = self.control_frame(self._control_payload(p.decision))
            ok_ctrl, _why = self.verify_control(frame, sig)
            if not ok_ctrl:
                self.frames_rejected += 1
                p.attempts += 1
                p.next_try = self.slot + 1
                return False
            self.auth_syncs += 1
            before = {"hop_mode": self.hop_mode, "dwell_channel": self.channel, "anti_follow": self.anti_follow,
                      "boost_db": self.net.tx_boost_db, "modulation": self.net.modulation, "delivery": self.recent_delivery()}
            self._apply(p.decision)
            if self.guard is not None and p.decision.action != "undo":
                self.guard.applied(p.decision, self, before)
            latency = None
            if self.resp_start is not None and p.decision.action != "undo":
                # first jammed slot -> first countermeasure of any kind in force across the network
                self.response_latencies.append(round((self.slot + 1 - self.resp_start) * SLOT_S * 1000, 1))
                self.resp_start = None
            if p.decision.action in ("hop", "fast_hop") and self.jam_start is not None:
                latency = round((self.slot + 1 - self.jam_start) * SLOT_S * 1000, 1)
                self.latencies.append(latency)
                self.jam_start = None
            self._emit_decision(p.decision, latency)
            self.pending = None
            self.loss5.clear()          # judge the new channel on its own packets, not the old ones
            for nid in missed:          # up to 20 % may have missed it: they stay on the old settings until resynced
                self.mark_desynced(nid, f"{nid} missed the control frame")
            return True
        else:
            p.attempts += 1
            p.next_try = self.slot + min(2 ** (p.attempts - 1), 4)     # retry with back-off
            if p.attempts == 6:
                self.outbox.append(envelope("event", SRC, world=self.world_id, level="warn",
                                            text="Control frame not getting through yet; retrying across rendezvous channels."))
        return False

    @staticmethod
    def _control_payload(d: Decision) -> bytes:
        """Compact encoding of the actions (what both ends need to compute the same next channel)."""
        parts = []
        for a in d.actions:
            if a.kind == "hop":
                parts.append(f"H{a.channel}")
            elif a.kind == "mode":
                parts.append(f"M{a.mode[0]}{'F' if a.anti_follow else ''}")
            elif a.kind == "power":
                parts.append(f"P{a.delta_db:+.0f}")
            elif a.kind == "rate":
                parts.append(f"R{a.modulation}")
            elif a.kind == "blacklist":
                parts.append("B" + "".join(map(str, a.channels)) + f"/{a.ttl_s:.0f}")
            elif a.kind == "relay":
                parts.append(f"X{a.between[0]}{a.between[1]}")
        return ";".join(parts).encode()[:64]

    def _clock_tick(self, dt: float) -> None:
        if self.clock is None:
            self.clock = TimeSync([n.id for n in self.net.nodes], self.seed)
        usable = {n.id for n in self.net.nodes if n.id not in self.revoked_nodes and not bat.away(n) and n.id not in self.desynced}
        heard = {n: (self.node_ema.get(n, 1.0) >= 0.5 and n in usable) for n in usable}
        for nid in self.clock.tick(self.t, dt, heard, usable):
            if nid in self.net.index and nid not in self.desynced and nid not in self.revoked_nodes:
                err = abs(self.clock.clocks[nid].err) * 1e3
                self.mark_desynced(nid, f"{nid}'s clock is {err:.2f} ms off the time master, outside the {GUARD_S * 1e3:.1f} ms guard")
        for level, text in self.clock.events:
            self.event(level, text)
        self.clock.events.clear()

    def mark_desynced(self, nid: str, why: str) -> None:
        if nid in self.desynced or nid not in self.net.index:
            return
        self.desynced[nid] = self.slot
        self.outbox.append(envelope("event", SRC, world=self.world_id, level="warn",
                                    text=f"{why}: it is still on the old channel settings. It listens on the key-derived "
                                         f"rendezvous channel; the command post sends a resync beacon there every {RESYNC_EVERY * 10} ms."))

    def _resync(self) -> None:
        """Every RESYNC_EVERY slots the command post sends a short authenticated beacon (current channel plan,
        hop mode, epoch) on the rendezvous channel, with the robust control format. An out-of-sync node that
        hears it rejoins. The rendezvous channel moves through a key-derived sequence, so one jammed channel
        cannot keep a node out for long."""
        if not self.desynced or self.slot % RESYNC_EVERY:
            return
        # (nodes that are out of sync stop transmitting data and listen on the rendezvous channel)
        rdv = self.rendezvous(self.slot // RESYNC_EVERY)
        ok = self.net.probe(self.jammers, rdv, self.rng["ctrl"], extra_db=CONTROL_MARGIN_DB)
        for nid, since in list(self.desynced.items()):
            if nid not in self.net.index:
                del self.desynced[nid]
                continue
            if self.slot - since < LOST_DETECT_SLOTS:
                continue                       # it has not noticed yet that it lost the network
            path = self.net.path_links(nid)
            if path and bool(np.all(ok[path])):
                ms = round((self.slot - since) * SLOT_S * 1000, 0)
                self.resync_ms.append(ms)
                del self.desynced[nid]
                self.auth_syncs += 1
                if self.clock is not None:
                    self.clock.resynced(nid, self.t)          # the rendezvous beacon also carries the master's time
                self.outbox.append(envelope("event", SRC, world=self.world_id, level="good",
                                            text=f"{nid} heard the resync beacon on rendezvous channel {rdv} and is back in sync after {ms:.0f} ms."))

    def _track_lost_nodes(self) -> None:
        """A node that hears nothing for a while listens on the key-derived rendezvous channel to rejoin."""
        for nid, e in self.node_ema.items():
            if nid == "CP":
                continue
            if e < 0.15 and nid not in self.lost_nodes:
                self.lost_nodes.add(nid)
                self.outbox.append(envelope("event", SRC, world=self.world_id, level="warn",
                                            text=f"{nid} lost contact: listening on rendezvous channel {self.rendezvous()} to rejoin."))
            elif e > 0.7 and nid in self.lost_nodes:
                self.lost_nodes.discard(nid)
                self.outbox.append(envelope("event", SRC, world=self.world_id, level="good", text=f"{nid} rejoined the network."))

    # ---------- security tests (press a button, see the attack fail) ----------
    def security_test(self, test: str) -> dict:
        if test == "desync":
            nid = next((n for n in ("D3", "D2", "D1", "V1") if n in self.net.index and n not in self.desynced), None)
            if nid is None:
                return {"test": "desync", "accepted": False, "reason": "Every node is already re-syncing."}
            self.mark_desynced(nid, f"Test: {nid} dropped the last channel-switch frame")
            return {"test": "desync", "accepted": True,
                    "reason": f"{nid} is now out of sync (it missed a channel switch). Watch it go 'rejoining' and come back "
                              f"via the rendezvous channel: the log reports how many milliseconds it took."}
        src, epoch = NODE_ADDR["D1"], self.epoch
        d1 = self.net.nodes[self.net.index["D1"]].pos
        if test in ("impersonate", "capture", "reboot"):
            return self._key_test(test)
        if test == "clock_loss":
            if self.clock is None:
                self._clock_tick(0.0)
            if not self.clock.master_alive or self.clock.master != "CP":
                return {"test": test, "accepted": False, "reason": f"The command post's clock is already gone; {self.clock.master} is time master."}
            self.clock.lose_master(self.t)
            return {"test": test, "accepted": True,
                    "reason": "The command post's clock stopped (destroyed, jammed or failed). Radios keep hopping on their own "
                              f"drift estimates; after 3 s without beacons the lowest-address healthy radio becomes time master. "
                              f"Worst clock error right now: {self.clock.max_error_us():.0f} us (guard {GUARD_S * 1e6:.0f} us)."}
        legit = self.seal_from("D1", sec.telemetry(src, self.t, d1, 0), epoch)
        hdr = sec.HEADER.unpack(legit[:sec.HEADER_LEN])
        fields = {"version": hdr[0], "type": "data", "src": "D1", "dst": "CP", "epoch": hdr[4], "seq": hdr[5]}
        if test == "legit":
            res = self.rx.open(legit, epoch)
            info = sec.parse_telemetry(res.payload) if res.ok else {}
            return {"test": test, "accepted": res.ok, "reason": f"Genuine frame from D1: {res.reason}. Decrypted telemetry: position {info.get('pos')}.",
                    "header_hex": legit[:10].hex(), "ciphertext_hex": legit[10:].hex(), "fields": fields}
        if test == "tamper":
            # an attacker intercepts a genuine frame, flips one bit of the ciphertext and forwards it
            bad = bytearray(legit)
            bad[10 + 5] ^= 0x08
            res = self.rx.open(bytes(bad), epoch)
            self.frames_rejected += 0 if res.ok else 1
            return {"test": test, "accepted": res.ok, "reason": f"Rejected: one bit flipped in the ciphertext → {res.reason}.",
                    "header_hex": bytes(bad[:10]).hex(), "ciphertext_hex": bytes(bad[10:]).hex(), "fields": fields}
        if test == "spoof":
            fake = sec.seal(sec.KeySchedule(self.attacker_key), sec.T_DATA, src, NODE_ADDR["CP"], epoch, self.next_seq("D1"),
                            sec.telemetry(src, self.t, (0, 0, 0), 9))
            res = self.rx.open(fake, epoch)
            self.frames_rejected += 0 if res.ok else 1
            return {"test": test, "accepted": res.ok, "reason": f"Rejected: an attacker forged a frame 'from D1' without D1's key → {res.reason}.",
                    "header_hex": fake[:10].hex(), "ciphertext_hex": fake[10:].hex(), "fields": fields}
        if test == "replay":
            self.rx.open(legit, epoch)                       # genuine frame accepted once
            res = self.rx.open(legit, epoch)                 # the same bytes sent again by an attacker
            self.frames_rejected += 0 if res.ok else 1
            return {"test": test, "accepted": res.ok, "reason": f"Rejected: an exact copy of a genuine frame was resent → {res.reason}.",
                    "header_hex": legit[:10].hex(), "ciphertext_hex": legit[10:].hex(), "fields": fields}
        # eavesdrop: the attacker records the frame and tries to read it with a guessed key
        guess = sec.KeySchedule(sec.random_key())
        try:
            guess.aead(epoch).decrypt(sec.nonce(src, epoch, hdr[5]), legit[10:], legit[:10])
            learnt = "decrypted (should never happen)"
        except InvalidTag:
            learnt = "nothing: decryption with a guessed key fails the tag check"
        self.rx.open(legit, epoch)
        return {"test": "eavesdrop", "accepted": False,
                "reason": f"An eavesdropper records only ciphertext. Position, status and commands are hidden; it learns {learnt}. "
                          f"Only the 10-byte header (sender, sequence) is visible, and changing it breaks the tag.",
                "header_hex": legit[:10].hex(), "ciphertext_hex": legit[10:].hex(), "plaintext_hint": "unreadable", "fields": fields}

    def _key_test(self, test: str) -> dict:
        """Key-management attacks: a captured radio impersonating another, revocation, and a reboot."""
        victim = next((n for n in ("D3", "D2", "D4") if n in self.radios and n in self.net.index and n not in self.revoked_nodes), None)
        if victim is None:
            return {"test": test, "accepted": False, "reason": "No drone left to use for this test."}
        epoch = self.epoch
        if test == "impersonate":
            # the enemy has captured `victim` with its keys and sends a frame claiming to be D1
            stolen = self.radios[victim].session
            fake = sec.seal(stolen, sec.T_DATA, NODE_ADDR["D1"], NODE_ADDR["CP"], epoch, self.next_seq("D1"),
                            sec.telemetry(NODE_ADDR["D1"], self.t, (0, 0, 0), 9))
            res = self.rx.open(fake, epoch)
            self.frames_rejected += 0 if res.ok else 1
            return {"test": test, "accepted": res.ok,
                    "reason": f"A captured {victim}, with all its keys, sent a frame claiming to be D1 → {res.reason}. "
                              f"Each radio has its own key, so one captured unit cannot pose as another."}
        if test == "capture":
            grants = self.km.revoke(victim)
            for n, g in grants.items():
                self.radios[n].accept_grant(g)
            self.revoked_nodes.add(victim)
            self.desynced.pop(victim, None)
            old = self.radios[victim]
            frame = sec.seal(old.session, sec.T_DATA, old.addr, NODE_ADDR["CP"], epoch, 0, sec.telemetry(old.addr, self.t, (0, 0, 0), 9))
            res = self.rx.open(frame, epoch)
            follows = old.group is not None and old.group.key("hop", epoch) == self.ks.key("hop", epoch)
            self.event("alert", f"{victim} reported captured: revoked. New group key (generation {self.km.generation}) sent to the "
                                f"{len(grants)} remaining radios, each wrapped in its own device key.")
            return {"test": test, "accepted": res.ok,
                    "reason": f"{victim} revoked → its frames: {res.reason}. Its old group key "
                              f"{'still' if follows else 'no longer'} predicts the hop pattern, so it cannot follow or jam it. "
                              f"New mission restores it."}
        # reboot: RAM lost, packet counter back to 0; the radio JOINs again and gets a fresh session key
        r = self.radios[victim]
        before = r.session.fingerprint()
        r.reboot()
        self._join(r)
        if self.clock is not None:
            self.clock.reboot(victim)                  # its clock restarts tens of ms off: the guard check will notice
        self.seq.pop(victim, None)
        after = r.session.fingerprint()
        frame = self.seal_from(victim, sec.telemetry(r.addr, self.t, (0, 0, 0), 0))
        res = self.rx.open(frame, epoch)
        return {"test": test, "accepted": res.ok,
                "reason": f"{victim} rebooted: its packet counter restarted at {sec.HEADER.unpack(frame[:sec.HEADER_LEN])[5]}, "
                          f"but it re-joined and got a new session key ({before} → {after}), so no AES-GCM nonce repeats under "
                          f"one key. First frame after reboot: {res.reason}."}

    def _apply(self, d: Decision) -> None:
        for a in d.actions:
            if a.kind == "hop" and a.channel is not None:
                self.hop_mode = "dwell"
                self.dwell_channel = a.channel
                self.hops += 1
            elif a.kind == "mode" and a.mode:
                if a.mode == "dwell" and self.hop_mode != "dwell":
                    self.dwell_channel = a.channel if a.channel is not None else self.channel
                if a.mode == "keyed":
                    self.anti_follow = self.anti_follow or a.anti_follow
                else:
                    self.anti_follow = False
                self.hop_mode = a.mode
                self.hops += 1
            elif a.kind == "power":
                self.net.tx_boost_db = float(np.clip(self.net.tx_boost_db + a.delta_db, 0.0, POWER_CAP_DB))
            elif a.kind == "rate" and a.modulation:
                self.net.modulation = a.modulation
            elif a.kind == "blacklist":
                for c in a.channels:
                    self.blacklist[c] = self.t + a.ttl_s
                # keep at least half the band usable: only the 4 most recent entries stay active
                active = sorted(self.active_blacklist(), key=lambda c: self.blacklist[c], reverse=True)
                for c in active[MAX_BLACKLIST:]:
                    self.blacklist.pop(c, None)
            elif a.kind == "relay":
                self._dispatch_relay(*a.between)

    def _dispatch_relay(self, a_id: str, b_id: str) -> None:
        if any(n.kind == "relay" for n in self.net.nodes):
            return
        pa, pb = self.net.nodes[self.net.index[a_id]].pos, self.net.nodes[self.net.index[b_id]].pos
        tallest = max([o["size"][2] for o in self.net.obstacles] + [0.0])
        mid = (pa + pb) / 2
        mid[2] = tallest + 30.0
        relay = Node("R1", "relay", pos=mid)
        self.net.add_node(relay)
        self.node_ema["R1"] = 1.0
        self.net.update_geometry(self.t, self.jammers)     # link arrays must include the new node now
        self.net.route()
        self.outbox.append(envelope("event", SRC, world=self.world_id, level="good",
                                    text=f"Relay drone R1 on station at {mid[2]:.0f} m between {a_id} and {b_id}."))

    def _emit_decision(self, d: Decision, latency: Optional[float]) -> None:
        if d.actions:
            self.qos.order(["decision"])                  # the order travels to every radio as a command message
        msg = envelope("decision", SRC, world=self.world_id, sim_time_s=round(self.t, 3), confidence=round(d.confidence, 3),
                       action=d.action, latency_ms=latency, reason=d.reason, defender=self.defender_name,
                       node_ids=[n.id for n in self.net.nodes])
        msg["class"] = d.cls
        if d.from_channel is not None:
            msg["from_channel"] = d.from_channel
        if d.to_channel is not None:
            msg["to_channel"] = d.to_channel
        self.outbox.append(msg)
        text = d.reason if latency is None else f"{d.reason} Done in {latency:.0f} ms."
        level = "info" if d.action == "ignore" or d.cls in ("accidental", "clean") else "good"
        self.outbox.append(envelope("event", SRC, world=self.world_id, level=level, text=text))

    def _run_schedule(self) -> None:
        """Scenario timeline: switch scheduled jammers on and off at their times."""
        for e in self.schedule:
            j = e["spec"]
            if not e["live"] and self.slot >= e["on"] and (e["off"] is None or self.slot < e["off"]):
                route = scn.jammer_route(j)
                pos = route(self.t)[0] if route else (j["pos"] + [2.0])[:3] if len(j["pos"]) == 2 else j["pos"]
                jm = make_jammer(e["id"], j["type"], pos, j.get("power_dbm"), j.get("label"))
                jm.route = route
                self.launch(jm)
                e["live"] = True
            elif e["live"] and e["off"] is not None and self.slot >= e["off"]:
                self.jammers = [x for x in self.jammers if x.id != e["id"]]
                self._started.discard(e["id"])
                e["live"] = False

    def _apply_jammer_changes(self) -> None:
        if self._remove_ids:
            self.jammers = [j for j in self.jammers if j.id not in self._remove_ids]
            self._launch_queue = [j for j in self._launch_queue if j.id not in self._remove_ids]
            self._started -= self._remove_ids
            self._remove_ids = set()
        if self._clear_pending:
            self.jammers.clear()
            self._started.clear()
            self._clear_pending = False
            self.net.tx_boost_db = getattr(self.defender, "initial_boost_db", 0.0)
            self.net.modulation = getattr(self.defender, "initial_modulation", "QPSK")
            self.blacklist.clear()
            self.jam_start = None
            self.resp_start = None
            if self.hop_mode == "keyed" and self.defender_name != "fixed_hopping":
                self.anti_follow = False
                self.hop_mode = "dwell"
                self.dwell_channel = self.channel_for_slot(self.slot)
        if self._launch_queue:
            self.jammers.extend(self._launch_queue)
            self._launch_queue.clear()

    # ---------- per broadcast tick ----------
    def run_slots(self, n: int) -> None:
        for _ in range(n):
            self.step_slot()

    def kpis(self) -> dict[str, Any]:
        s = sum(x for x, _ in self.recent)
        d = sum(y for _, y in self.recent)
        lat = sorted(self.latencies)
        links = self.link_msgs()
        return {
            "delivery_pct": round(100 * d / s, 1) if s else 100.0,
            "delivery_total_pct": round(100 * self.total_delivered / self.total_sent, 1) if self.total_sent else 100.0,
            "delivery_attack_pct": round(100 * self.attack_delivered / self.attack_sent, 1) if self.attack_sent else None,
            "hop_latency_ms": round(float(np.median(lat)), 1) if lat else None,
            "hop_latency_p95_ms": round(float(np.percentile(lat, 95)), 1) if lat else None,
            "outage_ms": round(self.outage_ms, 1),
            "healthy_links": sum(1 for l in links if l["routed"] and l["status"] == "ok"),
            "total_links": sum(1 for l in links if l["routed"]),
            "hops": self.hops,
            "decision_time_ms": self.decision_time_ms,
            "frames_verified": self.total_delivered,
            "frames_rejected": self.frames_rejected,
            "frames_forged": self.frames_forged,
            "frames_forged_accepted": self.frames_forged_accepted,
            "clock_error_us": self.clock.max_error_us({n for n in self.net.index if n not in self.desynced and n not in self.revoked_nodes}) if self.clock else 0.0,
            "time_master": self.clock.master if self.clock else "CP",
            "qos": {c: v for c, v in self.qos.summary().items() if c != "buffered"},
            "auth_syncs": self.auth_syncs,
            "key_epoch": self.epoch,
            "resync_ms": round(float(np.median(self.resync_ms)), 0) if self.resync_ms else None,
            "out_of_sync": len(self.desynced),
            "safety_fixes": self.guard.fixes if self.guard else 0,
            "safety_undos": self.guard.undos if self.guard else 0,
        }

    def link_msgs(self) -> list[dict[str, Any]]:
        net = self.net
        routed = set()
        for nid, p in net.parent.items():
            if p is not None:
                routed.add(net.pair_index[tuple(sorted((net.index[nid], net.index[p])))])
        out = []
        for k, (i, j) in enumerate(net.pairs):
            is_routed = k in routed
            if not is_routed and net.sinr_ema[k] < 15:
                continue
            pdr = float(net.pdr_ema[k])
            status = "ok" if pdr >= 0.9 else ("degraded" if pdr >= 0.5 else "down")
            out.append({"a": net.nodes[i].id, "b": net.nodes[j].id, "sinr_db": round(float(net.sinr_ema[k]), 1),
                        "status": status, "channel": self.channel, "delivery_pct": round(100 * pdr, 1), "routed": is_routed})
        return out

    def state_msg(self, view: str, paused: bool, speed: int) -> dict[str, Any]:
        net = self.net
        nodes = []
        for nd in net.nodes:
            e = self.node_ema.get(nd.id, 1.0)
            status = "ok" if nd.id == "CP" or e >= 0.9 else ("degraded" if e >= 0.5 else "down")
            if nd.id in self.lost_nodes or nd.id in self.desynced:
                status = "rejoining"
            if getattr(nd, "phase", "flying") in ("returning", "charging"):
                status = nd.phase
            if nd.id in self.revoked_nodes:
                status = "down"
            nodes.append({"id": nd.id, "kind": nd.kind, "pos": [round(float(v), 2) for v in nd.pos],
                          "heading_deg": round(nd.heading, 1), "channel": self.channel,
                          "tx_power_dbm": TX_POWER_DBM + net.tx_boost_db, "modulation": net.modulation, "status": status,
                          **({"battery_pct": round(nd.battery, 1)} if getattr(nd, "battery", None) is not None else {}),
                          **({"trip": nd.path.info(self.t)} if hasattr(nd.path, "info") else {}),
                          **({"parent": net.parent.get(nd.id)} if net.parent.get(nd.id) else {})})
        return envelope("state", SRC, world=self.world_id, tick=self.tick, sim_time_s=round(self.t, 3),
                        ai_enabled=self.ai_enabled, defender=self.defender_name, hop_mode=self.hop_mode, anti_follow=self.anti_follow, view=view,
                        mode="scenario" if self.scenario else "arena", scenario=self.scenario["id"] if self.scenario else None,
                        paused=paused, speed=speed, nodes=nodes, links=self.link_msgs(),
                        jammers=[j.to_msg() for j in self.jammers], estimates=getattr(self.defender, "estimates", []),
                        obstacles=net.obstacles, blacklist=sorted(self.active_blacklist()), kpis=self.kpis())

    def spectrum_msg(self) -> dict[str, Any]:
        w = self.sense("CP")
        self.last_window = w
        bins = mw_to_dbm(dbm_to_mw(w).mean(axis=0))
        msg = envelope("spectrum", SRC, world=self.world_id, node_id="CP", f_start_mhz=F_START_MHZ, f_stop_mhz=F_STOP_MHZ,
                       bins=[round(float(b), 1) for b in bins], anomaly=bool(np.max(bins) > NOISE_INBAND_DBM - 6 + 20))
        extra = getattr(self.defender, "spectrum_extras", None)
        if callable(extra):
            msg.update(extra(w))
        return msg

    def tick_messages(self, view: str, paused: bool, speed: int, with_spectrum: bool = True) -> list[dict]:
        self.tick += 1
        out = [self.state_msg(view, paused, speed)]
        if with_spectrum and self.tick % self.spectrum_every == 0:
            out.append(self.spectrum_msg())
        out.extend(self.outbox)
        self.outbox = []
        return out


class SimArena:
    """World interface for the server."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.seed = cfg.seed
        self.phy = get_phy(cfg.signal_backend)
        self.view = "single"
        self.ai_enabled = True
        self.paused = False
        self.speed = 1
        self.leaderboard: dict[str, int] = {}
        self._acc = 0.0
        self._jid = 0
        self._active: list[tuple] = []        # (type, pos, power, owner) currently deployed
        self.mission = 1
        self.log = MissionLog()
        self.last_report: Optional[dict] = None
        self.finished_report: Optional[dict] = None    # the result on screen right now: resent to screens that (re)connect
        self.replay: Optional[dict] = None      # recorded spectrum feeding the CNN panel (ai/sources.py)
        self.overrun_ticks = 0                 # ticks that hit the CPU budget (shown in /health)
        self._ticks = 0
        self._cost_ema = 0.0                   # measured CPU per tick (s), drives the spectrum frame rate
        self.scenario: Optional[dict] = None
        self._steps_done = 0
        self._scn_decisions: list[dict] = []
        self._scn_reported = False
        self._bench_thread: Optional[threading.Thread] = None
        self._bench_q: "queue.Queue[dict]" = queue.Queue()
        self.last_benchmark: Optional[dict] = None
        self._build()
        if self.phy.name != cfg.signal_backend:
            self._notice = f"Signal backend '{cfg.signal_backend}' unavailable; using '{self.phy.name}'."
        else:
            self._notice = None

    def _build(self, relaunch_after_s: Optional[float] = None) -> None:
        self.finished_report = None          # a new or restarted run: the old result is no longer on screen
        if self.view == "single":
            self.worlds = {"main": SimWorld("main", self.seed, AI_ON_DEFENDER if self.ai_enabled else AI_OFF_DEFENDER, self.phy,
                                            mission=self.mission, scenario=self.scenario, live=True)}
        else:
            self.worlds = {"ai_off": SimWorld("ai_off", self.seed, AI_OFF_DEFENDER, self.phy, spectrum_every=2, mission=self.mission,
                                              scenario=self.scenario, live=True),
                           "ai_on": SimWorld("ai_on", self.seed, AI_ON_DEFENDER, self.phy, mission=self.mission, scenario=self.scenario,
                                             live=True)}
        self._live: list[str] = []            # ids of user-launched jammers on the air, oldest first
        self._steps_done = 0
        self._scn_decisions = []
        self._scn_reported = False
        self.focus = "ai_on"                  # scenario: which defence every screen shows
        self.focus_manual = False             # a person picked it, so captions stop switching it
        self._acc = 0.0
        self._relaunch_at = relaunch_after_s if self._active else None
        if hasattr(self, "log"):
            self.log.start(label_for(self.scenario, self.mission, self.view))

    def _new_jammer_set(self, jtype, pos, power, owner) -> str:
        self._jid += 1
        jid = f"J{self._jid}"
        self._live.append(jid)
        for w in self.worlds.values():
            w.launch(make_jammer(jid, jtype, pos, power, owner))
        return jid

    # ---------- World protocol ----------
    def hello_info(self) -> dict[str, Any]:
        w = next(iter(self.worlds.values()))
        return {"server_version": __version__, "schema_version": "1.0", "world_source": "sim", "signal_backend": self.phy.name,
                "channels_mhz": CHANNELS_MHZ, "fft_bins": FFT_BINS, "bounds": ARENA_BOUNDS,
                "jammer_types": list(JAMMER_TYPES), "classes": CNN_CLASSES,
                "leaderboard": [{"player": p, "attacks": n} for p, n in sorted(self.leaderboard.items(), key=lambda kv: -kv[1])],
                "ai_enabled": self.ai_enabled, "view": self.view, "key_fingerprint": fingerprint(w.key), "mission": self.mission,
                "ai_off_defender": AI_OFF_DEFENDER, "ai_on_defender": w.defender_name if w.ai_enabled else AI_ON_DEFENDER,
                "obstacles": w.net.obstacles, "scenario": self.scenario["id"] if self.scenario else None,
                "theme": self.scenario["theme"] if self.scenario else "field",
                "time_of_day": self.scenario.get("time_of_day", "night") if self.scenario else "night",
                "scenarios": scn.ids()}

    def _start_benchmark(self, seed: Optional[int], seeds: int, duration_s: float, quick: bool) -> None:
        from .benchmark import run_benchmark
        defenders = ["fixed_hopping", "aegis"] if quick else ["fixed_hopping", "max_power", "afh_power", "aegis"]   # the fair line-up
        workers = max(1, min(3, (os.cpu_count() or 2) - 2))       # leave cores for the live demo
        seed = self.seed if seed is None else seed

        def work():
            last = [0.0]

            def progress(p, what):
                if p - last[0] >= 0.04 or p >= 1:
                    last[0] = p
                    self._bench_q.put(envelope("benchmark", SRC, status="running", progress=round(p, 3), seed=seed,
                                               seeds=seeds, current=what, placeholder=False))
            try:
                res = run_benchmark(seed=seed, seeds=seeds, duration_s=duration_s, defenders=defenders, workers=workers,
                                    progress=progress)
                self.last_benchmark = res
                self._bench_q.put(envelope("benchmark", SRC, status="done", progress=1.0, seed=seed, seeds=seeds,
                                           duration_s=duration_s, rows=res["rows"], overall=res["overall"],
                                           files=res["files"], elapsed_s=res["elapsed_s"], placeholder=False))
                a = next(o for o in res["overall"] if o["defender"] == "aegis")
                f = next(o for o in res["overall"] if o["defender"] == "fixed_hopping")
                self._bench_q.put(envelope("event", SRC, level="good",
                                           text=f"Benchmark done: ABHEDYA {a['delivery_pct']}% delivered vs fixed hopping "
                                                f"{f['delivery_pct']}%, beating it on {a['wins_vs_fixed']}/{len(res['jammers'])} jammer types. Saved CSV and JSON."))
            except Exception as exc:                                  # never take the live demo down
                log.exception("benchmark failed")
                self._bench_q.put(envelope("benchmark", SRC, status="failed", progress=0.0, placeholder=False, error=str(exc)))
                self._bench_q.put(envelope("event", SRC, level="warn", text=f"Benchmark failed: {exc}"))

        self._bench_thread = threading.Thread(target=work, name="benchmark", daemon=True)
        self._bench_thread.start()

    def step(self, dt: float) -> list[dict]:
        t_tick = time.perf_counter()
        out: list[dict] = []
        while not self._bench_q.empty():
            out.append(self._bench_q.get_nowait())
        if self._notice:
            out.append(envelope("event", SRC, level="warn", text=self._notice))
            self._notice = None
        if not self.paused:
            self._acc += dt * self.speed
            n = int(self._acc / SLOT_S + 1e-9)
            # never more than 2x the normal work per tick: an overloaded laptop slows simulated time a
            # little instead of falling further behind each tick (no death spiral, no frozen screens)
            n = min(n, 2 * SLOTS_PER_TICK * self.speed)
            self._acc -= n * SLOT_S
            self._acc = min(self._acc, SLOT_S)
            # time budget: the worlds advance slot by slot in lockstep, and if this tick has already used
            # TICK_BUDGET_S of CPU the rest is dropped, so a busy laptop (4x, side by side, many jammers)
            # slows simulated time a little but every screen keeps getting 20 updates a second
            budget = TICK_BUDGET_10X_S if self.speed >= 10 else TICK_BUDGET_S
            t0 = time.perf_counter()
            worlds = list(self.worlds.values())
            for k in range(n):
                for w in worlds:
                    w.step_slot()
                if k >= 1 and time.perf_counter() - t0 > budget:
                    self.overrun_ticks += 1
                    break
            if self._relaunch_at is not None and next(iter(self.worlds.values())).t >= self._relaunch_at:
                self._relaunch_at = None
                for jtype, pos, power, owner in self._active:
                    self._new_jammer_set(jtype, pos, power, owner)
                out.append(envelope("event", SRC, level="alert", text="Same attack launched in both worlds."))
        # spectrum frames are the most expensive message: send them less often at 4x and 10x (nobody can follow faster)
        # and when this laptop is struggling (measured), so state updates keep their 20 Hz
        self._ticks += 1
        every = 4 if (self._cost_ema > 0.040 or self.speed >= 10) else 2 if (self._cost_ema > 0.025 or self.speed >= 4) else 1
        spectrum_now = not self.paused and self._ticks % every == 0
        for w in self.worlds.values():
            out.extend(w.tick_messages(self.view, self.paused, self.speed, with_spectrum=spectrum_now))
        if self.scenario:
            out = self._scenario_tick(out)
        if self.replay is not None and not self.paused and self._ticks % 2 == 0:
            out.append(self._replay_msg())
        self.log.observe(out, next(iter(self.worlds.values())).t)
        self._cost_ema = 0.9 * self._cost_ema + 0.1 * (time.perf_counter() - t_tick)
        return out

    def _replay_msg(self) -> dict:
        """Next window of the recording through the CNN and the open-set check, ten windows a second."""
        from ai.sources import classify
        rp = self.replay
        rec = rp["rec"]
        win, label, t = rec.window(rp["k"])
        rp["k"] += 1
        r = classify(rp["cnn"], win)
        st = rp["stats"]
        st["windows"] += 1
        if label is not None:
            st["labelled"] += 1
            st["correct"] += int(r["verdict"] == label)
        bins = mw_to_dbm(dbm_to_mw(win).mean(axis=0))
        msg = envelope("replay", SRC, active=True, file=rec.path.name, name=rec.name, source=rec.meta.get("source", ""),
                       simulated=bool(rec.meta.get("simulated", False)), window=(rp["k"] - 1) % rec.n_windows,
                       windows=rec.n_windows, t_s=round(t, 3), f_start_mhz=F_START_MHZ, f_stop_mhz=F_STOP_MHZ,
                       bins=[round(float(b), 1) for b in bins], verdict=r["verdict"], confidence=round(r["confidence"], 4),
                       novelty=round(r["novelty"], 2), probs={c: round(float(p), 4) for c, p in zip(CNN_CLASSES, r["probs"])},
                       stats=dict(st))
        if label is not None:
            msg["label"] = label
        if r["class"] != "clean":
            msg["gradcam"] = np.round(r["cam"], 2).tolist()
        return msg

    def _scenario_tick(self, out: list[dict]) -> list[dict]:
        sc = self.scenario
        t = next(iter(self.worlds.values())).t
        for m in out:
            if m["type"] == "state":
                m["scenario_t"] = round(t, 2)
                m["scenario_duration"] = sc["duration_s"]
                m["focus"] = self.focus
            elif m["type"] == "decision" and m.get("world") in ("ai_on", "main"):
                self._scn_decisions.append({"t": m["sim_time_s"], "class": m["class"], "action": m["action"], "reason": m["reason"],
                                            "latency_ms": m.get("latency_ms")})
        steps = sc.get("steps", [])
        while self._steps_done < len(steps) and t >= steps[self._steps_done]["t"]:
            st = steps[self._steps_done]
            if st.get("show") and not self.focus_manual:
                self.focus = st["show"]
            out.append(envelope("event", SRC, level="info", text=st["text"], caption=True, step=self._steps_done,
                                steps=len(steps), focus=self.focus))
            self._steps_done += 1
        if t >= sc["duration_s"] and not self._scn_reported:
            self._scn_reported = True
            self.paused = True
            out.append(self._scenario_report(t))
        return out

    def _scenario_report(self, t: float) -> dict:
        sc = self.scenario
        k = {wid: w.kpis() for wid, w in self.worlds.items()}
        on = k.get("ai_on") or k.get("main")
        off = k.get("ai_off")
        classes = sorted({d["class"] for d in self._scn_decisions if d["class"] not in ("clean", "unknown")})
        msg = envelope("report", SRC, scenario=sc["id"], title=sc["title"], jammer=sc["jammer"], duration_s=round(t, 1),
                       status="done", ai_on=on, expected=sc.get("expected"),
                       decisions=self._scn_decisions[:10], classes_seen=classes)
        if off:
            msg["ai_off"] = off
        # snapshot for the downloadable report (/api/report): kept until the next mission finishes
        self.finished_report = msg
        self.last_report = {"msg": msg, "scenario": sc, "finished_wall": time.time(), "speed": self.speed,
                            "rows": [dict(r) for r in self.log.rows], "csv": self.log.to_csv(), "log": self.log.summary()}
        return msg

    def handle(self, cmd: C.Command) -> list[dict]:
        if isinstance(cmd, (C.LoadScenario, C.ExitScenario, C.Restart, C.SetView, C.Play)):
            self.finished_report = None      # the screen moves on from the result card
        out = self._handle(cmd)
        if not isinstance(cmd, (C.LoadScenario, C.ExitScenario, C.Restart, C.SetView)):   # those start a fresh log
            self.log.observe(out, next(iter(self.worlds.values())).t)
        return out

    def _handle(self, cmd: C.Command) -> list[dict]:
        rid = {"req_id": cmd.id} if cmd.id else {}
        ev = lambda level, text: envelope("event", SRC, level=level, text=text, **rid)  # noqa: E731
        if isinstance(cmd, C.LaunchJammer):
            x = float(np.clip(cmd.pos[0], *ARENA_BOUNDS["x"]))
            y = float(np.clip(cmd.pos[1], *ARENA_BOUNDS["y"]))
            out = []
            if len(self._live) >= MAX_JAMMERS:   # keep a crowd of judges from overloading the laptop
                old = self._live.pop(0)
                self._active.pop(0)
                for w in self.worlds.values():
                    w.remove_jammer(old)
                out.append(ev("info", f"At most {MAX_JAMMERS} jammers at once: switched off the oldest, {old}."))
            jid = self._new_jammer_set(cmd.jammer_type, (x, y), cmd.power_dbm, cmd.player)
            self._active.append((cmd.jammer_type, (x, y), cmd.power_dbm, cmd.player))
            out.append(ev("alert", f"{cmd.player or 'Someone'} launched a {cmd.jammer_type} jammer ({jid}) at ({x:.0f}, {y:.0f})."))
            if cmd.player:
                self.leaderboard[cmd.player] = self.leaderboard.get(cmd.player, 0) + 1
                out.append(envelope("score", SRC, leaderboard=[{"player": p, "attacks": n} for p, n in
                                                               sorted(self.leaderboard.items(), key=lambda kv: -kv[1])]))
            return out
        if isinstance(cmd, C.ClearJammers):
            self._active.clear()
            self._live.clear()
            for w in self.worlds.values():
                w.clear_jammers()
            return [ev("good", "All jammers cleared.")]
        if isinstance(cmd, C.ToggleAI) and self.scenario:
            on = (self.focus != "ai_on") if cmd.enabled is None else cmd.enabled
            self.focus, self.focus_manual = ("ai_on" if on else "ai_off"), True
            return [envelope("event", SRC, level="info", focus=self.focus, **rid,
                             text=f"Showing AI {'ON (ABHEDYA)' if on else 'OFF (conventional fixed hopping)'}. Both keep running on the same attack.")]
        if isinstance(cmd, C.ToggleAI):
            self.ai_enabled = (not self.ai_enabled) if cmd.enabled is None else cmd.enabled
            if "main" in self.worlds:
                self.worlds["main"].set_defender(AI_ON_DEFENDER if self.ai_enabled else AI_OFF_DEFENDER)
            name = self.worlds["main"].defender_name if "main" in self.worlds else ""
            return [ev("info", f"AI defender {'ON' if self.ai_enabled else 'OFF'}"
                       + (" (conventional fixed-pattern hopping)." if not self.ai_enabled else f" ({name}).")) ]
        if isinstance(cmd, C.SetView):
            self.view = cmd.view
            self._build(relaunch_after_s=1.0)
            if cmd.view == "side_by_side":
                return [ev("info", "Side by side: AI OFF (fixed hopping, left) vs AI ON (right). Same seed, same attack from t = 1 s.")]
            return [ev("info", "Single view.")]
        if isinstance(cmd, C.Pause):
            self.paused = True
            return [ev("info", "Paused.")]
        if isinstance(cmd, C.Play):
            self.paused = False
            return [ev("info", "Playing.")]
        if isinstance(cmd, C.SetSpeed):
            self.speed = cmd.speed
            return [ev("info", f"Speed {cmd.speed}x.")]
        if isinstance(cmd, C.Replay):
            if cmd.name is None:
                self.replay = None
                return [envelope("replay", SRC, active=False, **rid), ev("info", "Spectrum panel back on the live simulator.")]
            from ai.cnn import CNN
            from ai.sources import RECORDINGS, Recording
            path = RECORDINGS / (cmd.name if cmd.name.endswith(".npz") else cmd.name + ".npz")
            if not path.is_file():
                return [envelope("error", SRC, code="not_found", message=f"No recording '{cmd.name}' in recordings/.", **rid)]
            try:
                rec = Recording(path)
            except Exception as e:
                return [envelope("error", SRC, code="bad_recording", message=f"{path.name}: {e}", **rid)]
            self.replay = {"rec": rec, "k": 0, "cnn": CNN(), "stats": {"windows": 0, "labelled": 0, "correct": 0}}
            kind = "simulator capture (for testing)" if rec.meta.get("simulated") else "recorded measurement"
            return [ev("info", f"Spectrum panel now replays '{rec.name}', a {kind} from {rec.meta.get('source', 'unknown source')}: "
                               f"{rec.n_windows} windows through the same CNN and unknown-detector the live AI uses.")]
        if isinstance(cmd, C.Restart):
            self._active.clear()
            self.paused = False
            self.mission += 1                     # new mission, new pre-loaded key
            self._build()
            if self.scenario:
                return [ev("info", f"Scenario '{self.scenario['title']}' restarted from t = 0."),
                        envelope("hello", SRC, session_id=f"scenario-{self.scenario['id']}", **self.hello_info())]
            fp = fingerprint(next(iter(self.worlds.values())).key)
            return [ev("info", f"New mission #{self.mission} from t = 0 with the same seed. Mission key rotated, fingerprint {fp}."),
                    envelope("hello", SRC, session_id="mission-" + str(self.mission), **self.hello_info())]
        if isinstance(cmd, C.LoadScenario):
            self.scenario = scn.get(cmd.name)
            self._active.clear()
            self.paused = False
            self.speed = 1
            self.view = "side_by_side"
            self._build()
            self.paused = True                    # every screen waits on the briefing until someone presses Start
            sc = self.scenario
            return [ev("info", f"Scenario {sc['order']}/7 · {sc['title']}: {sc['subtitle']}. Press Start; AI OFF and AI ON run together on the same seed."),
                    envelope("hello", SRC, session_id=f"scenario-{sc['id']}", **self.hello_info())]
        if isinstance(cmd, C.ExitScenario):
            self.scenario = None
            self._active.clear()
            self.paused = False
            self.view = "single"
            self._build()
            return [ev("info", "Back to the live arena."), envelope("hello", SRC, session_id="arena", **self.hello_info())]
        if isinstance(cmd, C.RunBenchmark):
            if self._bench_thread is not None and self._bench_thread.is_alive():
                return [envelope("error", SRC, code="busy", message="A benchmark is already running.", **rid)]
            seeds = 1 if cmd.quick else (cmd.seeds or 3)
            dur = cmd.duration_s or 12.0
            self._start_benchmark(cmd.seed, seeds, dur, bool(cmd.quick))
            what = "fixed hopping vs ABHEDYA" if cmd.quick else "fixed hopping, brute force, rule-based (no CNN) and ABHEDYA"
            return [ev("info", f"Benchmark started: every jammer type × {seeds} seed{'s' if seeds > 1 else ''}, {what}, "
                               f"identical seeded runs of {dur:.0f} s. The live demo keeps running."),
                    envelope("benchmark", SRC, status="running", progress=0.0, seeds=seeds, placeholder=False, **rid)]
        if isinstance(cmd, C.SecurityTest):
            w = self.worlds.get("main") or self.worlds["ai_on"]
            r = w.security_test(cmd.test)
            msg = envelope("security", SRC, world=w.world_id, **r, **rid)
            verdict = "ACCEPTED" if r["accepted"] else ("hidden" if cmd.test == "eavesdrop" else "REJECTED")
            level = "good" if (cmd.test == "legit") == r["accepted"] else "alert"
            return [msg, ev(level, f"Security test '{cmd.test}': {verdict}. {r['reason']}")]
        return [envelope("error", SRC, code="not_implemented", message=f"'{cmd.action}' arrives in a later phase.", **rid)]
