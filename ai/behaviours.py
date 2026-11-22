"""Multi-label interference assessment: several jamming behaviours can be present at once.

The CNN names ONE class per window. Real attacks overlap (a follower hiding under a sweep, a pulsed
jammer next to a random one), so on top of the CNN this tracker measures, over the last half second of
per-slot channel energies, which behaviours are present. Each detector is independent and explainable:

  wideband      most of the band is lit (barrage, fast sweep)          mean share of hot channels >= 70 %
  tracking      the jammer sits on OUR channel wherever we go          our channel stands out from the rest
                (follower, reactive, smart in follow/predict mode)     of the band, and stays hot across hops
  narrowband    a few channels are jammed, not following us            1-4 hot channels that are not ours
  intermittent  the interference switches on and off (pulsed)         on-time 15-85 % with frequent switching
  unknown       the CNN's open-set check says "not one of the 7"       set by the defender from the CNN

Under a wideband jammer every channel is hot, so 'tracking' is measured as the excess energy on our own
channel over the band median: a sweep alone gives about 0 dB, a follower underneath adds 0.7-6 dB on whatever
channel we use, depending on where it is (measured in the simulator, see tests/test_behaviours.py). The test
is statistical: the average excess over 0.5 s must be 4 standard errors above zero.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from sim.radio import NOISE_INBAND_DBM

HOT_DB = 10.0
WINDOW_SLOTS = 50                     # 0.5 s of slots
MIN_SLOTS = 30
WIDE_SHARE = 0.70
TRACK_EXCESS_DB = 0.6                 # under wideband: our channel this much above the band median, on average,
TRACK_T = 4.0                         # and at least this many standard errors above zero (not chance)
TRACK_HIT = 0.6                       # without wideband: our channel hot in this share of slots...
TRACK_HOPS = 3                        # ...while we changed channel at least this often
INTERMITTENT_ON = (0.15, 0.85)
INTERMITTENT_SWITCHES = 6
NAMES = ("wideband", "tracking", "narrowband", "intermittent", "unknown")


@dataclass
class Assessment:
    present: dict                     # name -> bool
    score: dict                       # name -> 0..1
    evidence: dict                    # name -> short text

    @property
    def detected(self) -> list[str]:
        return [n for n in NAMES if self.present.get(n)]

    def to_msg(self) -> dict:
        return {"detected": self.detected, "scores": {n: round(float(self.score.get(n, 0.0)), 2) for n in NAMES},
                "evidence": {n: self.evidence[n] for n in self.detected if n in self.evidence}}


class BehaviourTracker:
    def __init__(self) -> None:
        self.hist: deque = deque(maxlen=WINDOW_SLOTS)        # (energy over the floor per channel, our channel)
        self.unknown = False

    def update(self, energy_dbm: np.ndarray, channel: int) -> None:
        self.hist.append((np.asarray(energy_dbm, dtype=np.float64) - NOISE_INBAND_DBM, int(channel)))

    def assess(self) -> Assessment:
        present = {n: False for n in NAMES}
        score = {n: 0.0 for n in NAMES}
        ev: dict[str, str] = {}
        present["unknown"] = score["unknown"] = self.unknown
        if self.unknown:
            ev["unknown"] = "the CNN's open-set check flags a pattern outside its 7 classes"
        if len(self.hist) < MIN_SLOTS:
            return Assessment(present, score, ev)
        E = np.array([e for e, _ in self.hist])
        ch = np.array([c for _, c in self.hist])
        hot = E > HOT_DB
        share = hot.mean(axis=1)

        wide = float(share.mean())
        score["wideband"] = min(1.0, wide / WIDE_SHARE)
        if wide >= WIDE_SHARE:
            present["wideband"] = True
            ev["wideband"] = f"{wide:.0%} of the band lit on average"

        ours = E[np.arange(len(E)), ch]
        hops = int(np.count_nonzero(np.diff(ch)))
        if present["wideband"]:
            others = np.array([np.median(np.delete(e, c)) for e, c in zip(E, ch)])
            d = ours - others
            excess = float(np.mean(d))
            tstat = excess / (float(np.std(d)) / np.sqrt(len(d)) + 1e-9)
            score["tracking"] = float(np.clip(tstat / (2 * TRACK_T), 0, 1))
            if excess >= TRACK_EXCESS_DB and tstat >= TRACK_T:
                present["tracking"] = True
                ev["tracking"] = (f"our channel {excess:+.1f} dB above the rest of the jammed band across {hops} hops "
                                  f"({tstat:.0f} standard errors: not chance)")
        else:
            hit = float(np.mean(hot[np.arange(len(E)), ch]))
            score["tracking"] = hit if hops >= TRACK_HOPS else hit * 0.5
            if hit >= TRACK_HIT and hops >= TRACK_HOPS:
                present["tracking"] = True
                ev["tracking"] = f"our channel jammed in {hit:.0%} of slots across {hops} hops"

        if not present["wideband"]:
            not_ours = hot.copy()
            not_ours[np.arange(len(E)), ch] = False
            n_other = not_ours.sum(axis=1)
            narrow = float(np.mean((n_other >= 1) & (n_other <= 4)))
            score["narrowband"] = narrow
            if narrow >= 0.5:
                chans = np.flatnonzero(not_ours.mean(axis=0) >= 0.3)
                present["narrowband"] = True
                ev["narrowband"] = f"channels {', '.join(map(str, chans))} jammed" if len(chans) else "a few channels jammed"

        on = hot.any(axis=1)
        on_share = float(on.mean())
        switches = int(np.count_nonzero(np.diff(on.astype(int))))
        per_s = switches * (100.0 / len(on))
        lo, hi = INTERMITTENT_ON
        score["intermittent"] = float(min(1.0, per_s / (2 * INTERMITTENT_SWITCHES))) if lo <= on_share <= hi else 0.0
        if lo <= on_share <= hi and per_s >= INTERMITTENT_SWITCHES:
            present["intermittent"] = True
            ev["intermittent"] = f"on {on_share:.0%} of the time, switching {per_s:.0f} times a second"
        return Assessment(present, score, ev)
