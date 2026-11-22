"""Nodes, motion, obstacles, per-slot link budget, packet delivery and mesh routing."""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from .radio import (
    BUILDING_LOSS_DB, HILL_LOSS_DB, N_CH, NOISE_INBAND_DBM, PL_EXPONENT_AIR, PL_EXPONENT_GROUND, RICIAN_K_DB_AIR,
    RICIAN_K_DB_GROUND, TX_POWER_DBM, dbm_to_mw, mw_to_dbm, obstacle_loss_many, packet_delivery, path_loss_db,
    rician_gain_db,
)

from .trips import Trip

ROUTE_HYSTERESIS_DB = 3.0
SINR_EMA = 0.15                 # per-slot smoothing of link quality used for routing and colours
AIRBORNE = ("drone", "relay")

ARENA_OBSTACLES = [
    {"id": "B1", "kind": "building", "pos": [350.0, 380.0], "size": [70.0, 50.0, 40.0], "loss_db": BUILDING_LOSS_DB},
    {"id": "B2", "kind": "building", "pos": [640.0, -110.0], "size": [60.0, 60.0, 30.0], "loss_db": BUILDING_LOSS_DB},
    {"id": "B3", "kind": "building", "pos": [120.0, 560.0], "size": [50.0, 80.0, 28.0], "loss_db": BUILDING_LOSS_DB},
    {"id": "H1", "kind": "hill", "pos": [950.0, 430.0], "size": [220.0, 180.0, 70.0], "loss_db": HILL_LOSS_DB},
]


# arena convoy roads (the viewer draws the same polylines); both keep clear of buildings and the hill
ARENA_ROUTES = {
    "V1": [(-150, -250), (150, -285), (450, -235), (760, -265), (980, -200)],
    "V2": [(60, 760), (320, 720), (600, 660), (820, 620), (1120, 580), (1170, 380), (1080, 140)],
}


def _pingpong(s: float, length: float) -> float:
    s = s % (2 * length)
    return s if s <= length else 2 * length - s


def drone_path(cx: float, cy: float, rx: float, ry: float, w: float, phase: float, alt: float) -> Callable[[float], tuple]:
    def f(t: float):
        a = phase + w * t
        x, y = cx + rx * math.cos(a), cy + ry * math.sin(a)
        heading = math.degrees(math.atan2(ry * math.cos(a), -rx * math.sin(a))) % 360
        return (x, y, alt + 8 * math.sin(2 * a)), (90 - heading) % 360
    return f


def road_path(p0, p1, speed: float, offset_s: float) -> Callable[[float], tuple]:
    p0, p1 = np.array(p0, float), np.array(p1, float)
    length = float(np.linalg.norm(p1 - p0))
    d = (p1 - p0) / length

    def f(t: float):
        s_raw = (t * speed + offset_s) % (2 * length)
        s = _pingpong(t * speed + offset_s, length)
        p = p0 + d * s
        dirv = d if s_raw <= length else -d
        heading = (90 - math.degrees(math.atan2(dirv[1], dirv[0]))) % 360
        return (float(p[0]), float(p[1]), 1.5), heading
    return f


@dataclass
class Node:
    id: str
    kind: str
    path: Optional[Callable[[float], tuple]] = None
    pos: np.ndarray = field(default_factory=lambda: np.zeros(3))
    heading: float = 0.0

    def move(self, t: float) -> None:
        if self.path is not None:
            p, h = self.path(t)
            self.pos = np.array(p, float)
            self.heading = h


# convoy truck cruise speeds (m/s). The benchmark, robustness sweep and experiments keep the speeds their published
# numbers were measured at; the live arena on screen drives faster so the trucks visibly move on a 1.3 km map.
BENCH_TRUCK_SPEEDS = (9.0, 7.0)
LIVE_TRUCK_SPEEDS = (20.0, 16.0)


def arena_nodes(truck_speeds: tuple[float, float] = BENCH_TRUCK_SPEEDS) -> list[Node]:
    nodes = [Node("CP", "command_post", pos=np.array([0.0, 0.0, 6.0]))]
    nodes.append(Node("D1", "drone", drone_path(250, 150, 260, 170, 0.045, 0.0, 95)))
    nodes.append(Node("D2", "drone", drone_path(520, 420, 300, 220, -0.035, 1.2, 110)))
    nodes.append(Node("D3", "drone", drone_path(820, 150, 230, 260, 0.04, 2.6, 100)))
    nodes.append(Node("D4", "drone", drone_path(420, -150, 340, 140, -0.05, 4.0, 85)))
    nodes.append(Node("V1", "vehicle", Trip(ARENA_ROUTES["V1"], "Forward base", "Supply depot", truck_speeds[0])))
    nodes.append(Node("V2", "vehicle", Trip(ARENA_ROUTES["V2"], "Field hospital", "Radar site", truck_speeds[1], offset_s=40.0)))
    for n in nodes:
        n.move(0.0)
    return nodes


class Network:
    """Holds the nodes and computes, per slot, every link's SINR and whether its packet got through."""

    def __init__(self, nodes: list[Node], obstacles: list[dict]):
        self.nodes = nodes
        self.obstacles = obstacles
        self._rebuild_pairs()
        self.tx_boost_db = 0.0
        self.modulation = "QPSK"
        self.parent: dict[str, Optional[str]] = {}

    # ---------- topology ----------
    def _rebuild_pairs(self) -> None:
        n = len(self.nodes)
        self.index = {nd.id: i for i, nd in enumerate(self.nodes)}
        self.pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
        self.pa = np.array([p[0] for p in self.pairs], dtype=int)
        self.pb = np.array([p[1] for p in self.pairs], dtype=int)
        self.pair_index = {p: k for k, p in enumerate(self.pairs)}
        self.sinr_ema = np.full(len(self.pairs), 20.0)
        self.pdr_ema = np.ones(len(self.pairs))
        self.cp = self.index["CP"]

    def add_node(self, node: Node) -> None:
        old = {(self.nodes[i].id, self.nodes[j].id): (s, p) for (i, j), s, p in zip(self.pairs, self.sinr_ema, self.pdr_ema)}
        self.nodes.append(node)
        self._rebuild_pairs()
        for k, (i, j) in enumerate(self.pairs):
            key = (self.nodes[i].id, self.nodes[j].id)
            if key in old:
                self.sinr_ema[k], self.pdr_ema[k] = old[key]

    def remove_relays(self) -> None:
        self.nodes = [n for n in self.nodes if n.kind != "relay"]
        self._rebuild_pairs()

    # ---------- geometry (once per tick) ----------
    def update_geometry(self, t: float, jammers) -> None:
        for nd in self.nodes:
            nd.move(t)
        P = np.array([nd.pos for nd in self.nodes])
        self.positions = P
        d = np.linalg.norm(P[self.pa] - P[self.pb], axis=1)
        air = np.array([self.nodes[i].kind in AIRBORNE or self.nodes[j].kind in AIRBORNE for i, j in self.pairs])
        self.link_k_db = np.where(air, RICIAN_K_DB_AIR, RICIAN_K_DB_GROUND)
        obst = obstacle_loss_many(P[self.pa], P[self.pb], self.obstacles)
        self.link_obst = obst
        self.link_loss_db = path_loss_db(d, np.where(air, PL_EXPONENT_AIR, PL_EXPONENT_GROUND)) + obst
        self.link_dist = d
        # jammer -> node path gain (dB, negative), without fading
        self.jam_gain_db = np.zeros((len(jammers), len(self.nodes)))
        if jammers:
            nn = len(self.nodes)
            J = np.array([jm.pos for jm in jammers], float)
            exps = np.array([PL_EXPONENT_AIR if nd.kind in AIRBORNE else PL_EXPONENT_GROUND for nd in self.nodes])
            dj = np.linalg.norm(P[None, :, :] - J[:, None, :], axis=2)                       # (jammers, nodes)
            ob = obstacle_loss_many(np.repeat(J, nn, axis=0), np.tile(P, (len(J), 1)), self.obstacles).reshape(len(J), nn)
            self.jam_gain_db = -(path_loss_db(dj, exps[None, :]) + ob)

    # ---------- per slot ----------
    def interference_mw(self, jammers, channel: int, rng: np.random.Generator) -> np.ndarray:
        """In-band interference at every node on `channel`, linear mW (with Rician fading per jammer-node)."""
        total = np.zeros(len(self.nodes))
        for a, jm in enumerate(jammers):
            # always draw, so the random stream does not depend on our channel (twin worlds stay aligned)
            fade = rician_gain_db(rng, RICIAN_K_DB_GROUND, len(self.nodes))
            f = jm.frac[channel]
            if f > 0:
                total += dbm_to_mw(jm.power_dbm + self.jam_gain_db[a] + fade) * f
        return total

    def probe(self, jammers, channel: int, rng: np.random.Generator, extra_db: float = 0.0) -> np.ndarray:
        """One-off delivery draw on another channel (control frames), without touching the EMAs.
        extra_db: margin of the robust control format (power, BPSK, repetition coding)."""
        n_links = len(self.pairs)
        sig = TX_POWER_DBM + self.tx_boost_db + extra_db - self.link_loss_db + rician_gain_db(rng, self.link_k_db, n_links)
        i_mw = self.interference_mw(jammers, channel, rng)
        sinr = sig - mw_to_dbm(dbm_to_mw(NOISE_INBAND_DBM) + np.maximum(i_mw[self.pa], i_mw[self.pb]))
        return rng.random(n_links) < packet_delivery(sinr, self.modulation)

    def channel_energy_dbm(self, jammers, node_idx: int) -> np.ndarray:
        """Mean in-band interference + noise on all 8 channels at one node (wideband energy sensing)."""
        e = np.full(N_CH, dbm_to_mw(NOISE_INBAND_DBM))
        for a, jm in enumerate(jammers):
            e = e + dbm_to_mw(jm.power_dbm + self.jam_gain_db[a, node_idx]) * jm.frac
        return mw_to_dbm(e)

    def fused_energy_dbm(self, jammers) -> np.ndarray:
        """Distributed spectrum sensing: every node measures in-band energy on all 8 channels and reports
        it in its (encrypted) telemetry; the command post keeps the worst case per channel. A jammer that
        the command post itself cannot see (behind buildings) still shows up."""
        e = np.full((len(self.nodes), N_CH), dbm_to_mw(NOISE_INBAND_DBM))
        for a, jm in enumerate(jammers):
            e += dbm_to_mw(jm.power_dbm + self.jam_gain_db[a])[:, None] * jm.frac[None, :]
        return mw_to_dbm(e.max(axis=0))

    def slot(self, jammers, channel: int, rng_fade: np.random.Generator, rng_pkt: np.random.Generator):
        """Returns (sinr_db per pair, success per pair, interference_mw per node)."""
        n_links = len(self.pairs)
        sig = TX_POWER_DBM + self.tx_boost_db - self.link_loss_db + rician_gain_db(rng_fade, self.link_k_db, n_links)
        i_mw = self.interference_mw(jammers, channel, rng_fade)
        noise = dbm_to_mw(NOISE_INBAND_DBM)
        worst_i = np.maximum(i_mw[self.pa], i_mw[self.pb])         # the link must work both ways
        sinr = sig - mw_to_dbm(noise + worst_i)
        pdr = packet_delivery(sinr, self.modulation)
        ok = rng_pkt.random(n_links) < pdr
        self.sinr_ema = (1 - SINR_EMA) * self.sinr_ema + SINR_EMA * sinr
        self.pdr_ema = (1 - SINR_EMA) * self.pdr_ema + SINR_EMA * ok
        return sinr, ok, i_mw

    # ---------- routing (once per tick) ----------
    def route(self) -> None:
        """Widest path (best weakest-link SINR) to the command post, with 3 dB hysteresis."""
        n = len(self.nodes)
        q = {}
        for k, (i, j) in enumerate(self.pairs):
            q[(i, j)] = q[(j, i)] = self.sinr_ema[k]
        from .battery import relay_penalty_db
        relay_pen = [relay_penalty_db(nd) for nd in self.nodes]
        best = np.full(n, -np.inf)
        best[self.cp] = np.inf
        parent = [-1] * n
        heap = [(-np.inf, self.cp)]
        done = set()
        while heap:
            negw, u = heapq.heappop(heap)
            if u in done:
                continue
            done.add(u)
            for v in range(n):
                if v == u or v in done:
                    continue
                # passing through a drone that is low on battery (or flying home) costs extra: see battery.relay_penalty_db
                pen = 0.0 if u == self.cp else relay_pen[u]
                w = min(best[u], q[(u, v)] - pen)
                if w > best[v]:
                    best[v] = w
                    parent[v] = u
                    heapq.heappush(heap, (-w, v))
        widest = {self.nodes[v].id: (self.nodes[parent[v]].id if parent[v] >= 0 else None) for v in range(n)}
        widest["CP"] = None
        new_parent = dict(widest)
        for v in range(n):
            nid = self.nodes[v].id
            old = self.parent.get(nid)
            if nid == "CP" or old is None or old not in self.index or old == widest[nid]:
                continue
            ov = self.index[old]
            old_w = min(best[ov], q[(v, ov)])
            if old_w > best[v] - ROUTE_HYSTERESIS_DB:
                new_parent[nid] = old          # not clearly worse: keep it (stops flapping)
        # mixing old and new choices can create a loop; any node that no longer reaches CP reverts
        for _ in range(n):
            bad = [nid for nid in new_parent if nid != "CP" and new_parent[nid] is not None
                   and not self._reaches_cp(nid, new_parent)]
            if not bad:
                break
            for nid in bad:
                new_parent[nid] = widest[nid]
        self.parent = new_parent
        self.route_bottleneck = {self.nodes[v].id: float(best[v]) for v in range(n)}

    def _reaches_cp(self, start: str, parent: dict[str, Optional[str]]) -> bool:
        seen, cur = set(), start
        while cur is not None and cur not in seen:
            if cur == "CP":
                return True
            seen.add(cur)
            cur = parent.get(cur)
        return False

    def path_links(self, nid: str) -> list[int]:
        """Pair indices along nid's route to CP (empty if no route)."""
        out, seen, cur = [], set(), nid
        while cur != "CP":
            p = self.parent.get(cur)
            if p is None or cur in seen:
                return []
            seen.add(cur)
            i, j = sorted((self.index[cur], self.index[p]))
            out.append(self.pair_index[(i, j)])
            cur = p
        return out
