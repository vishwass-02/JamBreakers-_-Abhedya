"""Defenders: what the network does about interference.

A defender sees only what a real radio could measure (packet loss, wideband energy per channel, a
spectrogram on request) and never the ground-truth jammer list. Each slot it may return actions;
the world sends them as a control frame and they take effect only once that frame is delivered.

  none            stays on channel 0 forever
  fixed_hopping   conventional FHSS: a public repeating pattern, one channel per slot (the AI OFF side)
  energy          hops to the quietest channel when loss passes 10 % (no classification)
  fhss_keyed      secret fast hopping from the start: a keyed pseudo-random pattern (the standard military baseline)
  max_power       brute force: fixed hopping at maximum power (+9 dB) and BPSK from the start (no sensing at all)
  afh_power       the AI's whole toolbox chosen by simple rules, no classifier: blacklist hot channels and hop
                  over the rest (Bluetooth-style AFH), then raise power, then BPSK; step back down when quiet.
                  This is the ablation that shows what the CNN itself adds.
  aegis           the CNN defender (ai/defender.py, Phase 2)
  rl              a tabular Q-learning agent trained in this simulator (ai/rl.py, Phase 6)
  hybrid          AEGIS leads, the RL policy takes over when AEGIS is stuck or the attack keeps changing (Phase 8)
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from .radio import N_CH, NOISE_INBAND_DBM, POWER_CAP_DB, POWER_STEP_DB

LOSS_TRIGGER = 0.10


@dataclass
class Action:
    kind: str                     # 'hop' | 'mode' | 'power' | 'rate' | 'relay' | 'blacklist'
    channel: Optional[int] = None
    mode: Optional[str] = None    # 'dwell' | 'keyed' | 'fixed'
    delta_db: float = 0.0
    modulation: Optional[str] = None
    channels: tuple[int, ...] = ()
    ttl_s: float = 0.0
    between: tuple[str, str] = ("", "")
    anti_follow: bool = False     # keyed mode: also never reuse the last 3 channels (only needed against a follower)


@dataclass
class Decision:
    cls: str                      # CNN class or 'unknown'
    confidence: float
    action: str                   # schema action_taken
    reason: str
    actions: list[Action] = field(default_factory=list)
    from_channel: Optional[int] = None
    to_channel: Optional[int] = None
    decision_time_ms: Optional[float] = None


@dataclass
class SlotObs:
    slot: int
    t: float
    channel: int
    hop_mode: str
    sent: int
    delivered: int
    loss_recent: float                       # over the last 5 slots
    energy_dbm: np.ndarray                   # in-band energy per channel, fused from every node's reports (worst case)
    tx_boost_db: float
    modulation: str
    control_pending: bool
    blacklist: frozenset
    sense: Callable[[str], np.ndarray]      # spectrogram window (12 x 128 dBm) at a node, on request
    world: object = None                     # for observation helpers only (positions, link SINR)


class HopGuard:
    """At most `max_hops` hops in `window_s`, then hold for `hold_s` (stops thrashing)."""

    def __init__(self, max_hops: int = 3, window_s: float = 5.0, hold_s: float = 5.0):
        self.max_hops, self.window_s, self.hold_s = max_hops, window_s, hold_s
        self.times: deque[float] = deque()
        self.hold_until = -1.0

    def allowed(self, t: float) -> bool:
        while self.times and t - self.times[0] > self.window_s:
            self.times.popleft()
        return t >= self.hold_until

    def record(self, t: float) -> None:
        self.times.append(t)
        if len(self.times) >= self.max_hops:
            self.hold_until = t + self.hold_s
            self.times.clear()


class Defender:
    name = "base"
    initial_mode = "dwell"
    initial_boost_db = 0.0          # transmit power over nominal at start (dB)
    initial_modulation = "QPSK"

    def __init__(self, rng: Optional[np.random.Generator] = None):
        self.rng = rng or np.random.default_rng(0)

    def on_slot(self, obs: SlotObs) -> Optional[Decision]:
        return None


class NoDefense(Defender):
    name = "none"


class FixedHopping(Defender):
    name = "fixed_hopping"
    initial_mode = "fixed"


class EnergyDetector(Defender):
    """Classic adaptive radio: when loss passes 10 %, hop to the channel with the least energy."""
    name = "energy"

    def __init__(self, rng=None):
        super().__init__(rng)
        self.guard = HopGuard()

    def on_slot(self, obs: SlotObs) -> Optional[Decision]:
        if obs.control_pending or obs.loss_recent <= LOSS_TRIGGER or not self.guard.allowed(obs.t):
            return None
        e = obs.energy_dbm.copy()
        e[obs.channel] = np.inf
        to = int(np.argmin(e))
        self.guard.record(obs.t)
        return Decision("unknown", 0.0, "hop",
                        f"Loss {obs.loss_recent:.0%} on channel {obs.channel}; hopping to the quietest channel {to}.",
                        [Action("hop", channel=to)], obs.channel, to)


class KeyedFHSS(Defender):
    """Secret fast hopping from the first slot: every slot on a pseudo-random channel derived from the shared key."""
    name = "fhss_keyed"
    initial_mode = "keyed"


class MaxPower(Defender):
    """Brute force: conventional fixed hopping, but always at the power cap and the most robust modulation.
    It costs battery and data rate all the time, attack or not."""
    name = "max_power"
    initial_mode = "fixed"
    initial_boost_db = POWER_CAP_DB
    initial_modulation = "BPSK"


class RuleAdaptive(Defender):
    """Adaptive hopping plus power control by fixed rules, with no jammer classification (the CNN ablation)."""
    name = "afh_power"
    initial_mode = "keyed"
    HOT_DB = 10.0           # a channel this far above the noise floor is blacklisted
    STEP_S = 0.5            # act at most twice a second

    def __init__(self, rng=None):
        super().__init__(rng)
        self.last_t = -1e9
        self.quiet_since: Optional[float] = None

    def on_slot(self, obs: SlotObs) -> Optional[Decision]:
        if obs.control_pending or obs.t - self.last_t < self.STEP_S:
            return None
        if obs.loss_recent <= LOSS_TRIGGER:
            if obs.loss_recent < 0.02:
                self.quiet_since = obs.t if self.quiet_since is None else self.quiet_since
                if obs.t - self.quiet_since > 5.0 and (obs.tx_boost_db > 0 or obs.modulation != "QPSK"):
                    self.last_t, self.quiet_since = obs.t, obs.t
                    acts = [Action("rate", modulation="QPSK")] if obs.modulation != "QPSK" else [Action("power", delta_db=-POWER_STEP_DB)]
                    return Decision("unknown", 0.0, "lower_rate" if acts[0].kind == "rate" else "raise_power",
                                    "Band quiet for 5 s: stepping back down.", acts, obs.channel, obs.channel)
            return None
        self.quiet_since = None
        self.last_t = obs.t
        hot_all = [int(c) for c in range(N_CH) if obs.energy_dbm[c] > NOISE_INBAND_DBM + self.HOT_DB]
        wideband = len(hot_all) >= N_CH // 2            # most of the band is hot: avoiding channels cannot help
        hot = tuple(c for c in sorted(hot_all, key=lambda c: -obs.energy_dbm[c]) if c not in obs.blacklist)[:4]
        if hot and not wideband and len(obs.blacklist) < 4:
            return Decision("unknown", 0.0, "fast_hop", f"Loss {obs.loss_recent:.0%}: blacklisted hot channels {list(hot)}.",
                            [Action("blacklist", channels=hot, ttl_s=10.0), Action("mode", mode="keyed")], obs.channel, None)
        if obs.tx_boost_db < POWER_CAP_DB:
            return Decision("unknown", 0.0, "raise_power", f"Loss {obs.loss_recent:.0%} with no hot channel left: +3 dB.",
                            [Action("power", delta_db=POWER_STEP_DB)], obs.channel, obs.channel)
        if obs.modulation == "QPSK":
            return Decision("unknown", 0.0, "lower_rate", "At full power and still losing: QPSK -> BPSK.",
                            [Action("rate", modulation="BPSK")], obs.channel, obs.channel)
        return None


DEFENDERS: dict[str, type[Defender]] = {"none": NoDefense, "fixed_hopping": FixedHopping, "energy": EnergyDetector,
                                        "fhss_keyed": KeyedFHSS, "max_power": MaxPower, "afh_power": RuleAdaptive}


def make_defender(name: str, rng: np.random.Generator) -> Defender:
    if name == "rl":
        from ai.rl import RLDefender
        return RLDefender(rng)
    if name == "hybrid":
        from ai.defender import HybridDefender
        return HybridDefender(rng)
    if name == "aegis":
        try:
            from ai.defender import AegisDefender  # Phase 2
            return AegisDefender(rng)
        except ImportError:
            name = "energy"
    return DEFENDERS[name](rng)
