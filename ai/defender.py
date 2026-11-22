"""ABHEDYA defender: sense -> classify -> decide.

Trigger: packet loss over the last 5 slots above 10 %. Then capture 3 spectrogram windows at the
receiver that is losing packets, run the CNN on each and average (the 3-window vote), and respond:

  barrage     covers the whole band, so hopping is pointless: raise power (+3 dB steps, cap +9 dB),
              then drop QPSK -> BPSK (+3 dB more margin)
  sweep       crosses every channel, so hopping only thrashes: raise power and hold, then BPSK
  random      blacklist the channels it is sitting on and hop to the cleanest one left
  reactive    it listens on one channel: switch to the key-derived fast hop sequence and blacklist
              the channel it is listening on, so it rarely hears us again
  deceptive   look-alike frames (authentication rejects them): hop away and blacklist that channel
  accidental  a friendly emitter, not an attack: no alarm, quietly move to a clean channel
  clean       loss but nothing in the spectrum: a link is blocked (building, hill): send a relay drone

Behaviour over time:
  follower    if the jammer lands on our new channel within 0.8 s of a hop, twice, it is following:
              switch to the key-derived fast hop sequence (it cannot predict HMAC(key, slot))
  hop guard   at most 3 hops in 5 s; when it trips, go to keyed fast hopping instead of sitting still
  recovery    when the band has been quiet for a while, step power back down, return to QPSK and
              leave fast-hop mode

It also estimates where the jammer is (RSSI least squares) every 0.5 s while the band is noisy.
"""
from __future__ import annotations

from collections import deque
from typing import Optional

import time

import numpy as np

from sim.defenders import Action, Decision, Defender, HopGuard, LOSS_TRIGGER, SlotObs
from ai.behaviours import BehaviourTracker
from ai.ood import UNKNOWN_RATIO, is_unknown
from ai.ood import ratio as ood_ratio
from sim.radio import N_CH, NOISE_INBAND_DBM, POWER_CAP_DB, POWER_STEP_DB

from .cnn import load_shared
from .features import CLASSES
from .localize import ANNOUNCE_RADIUS_M, PUBLISH_RADIUS_M, locate

HOT_DB = 10.0                 # channel counts as occupied if its energy is this far above the noise floor
DISPLAY_CNN_EVERY_S = 0.2     # spectrum panel: CNN bars / Grad-CAM refresh rate (display only)
POWER_CAP_LOW_BATTERY_DB = 6.0   # max boost while any drone is below battery.LOW_AT
SURVIVAL_LOSS = 0.5           # still losing this much at full power + BPSK + after the probe -> survival mode
PROBE_S = 2.0                 # how long to test keyed hopping under a wideband jammer
PROBE_EVERY_S = 10.0          # and how often at most
PROBE_GAIN = 0.10             # keep it only if loss drops by at least this much
NARROW_ON_WIDE_DB = 8.0
ASSESS_EVERY_S = 0.25                # multi-label assessment refresh
TRACK_LATCH_S = 3.0                  # keep 'tracking' after the last positive check       # under a sweep/barrage: a channel this far above the band median has a second, narrowband jammer on it
VOLATILE_SPAN_S = 5.0                # referee: look at the last 5 s of assessments
VOLATILE_RATE = 0.4                  # behaviour changes per second that count as a tactic-switching attack
VOLATILE_JUDGE_S = 2.0
VOLATILE_TRIAL_S = 2.0
SCORE_MIN_N = 3                      # seconds of evidence before the scoreboard overrides a default
SCORE_MARGIN = 0.05
STACKED_LATCH_S = 10.0               # several jammers do not vanish in a second: keep 'stacked' for 10 s
FOLLOW_WINDOW_S = 0.3
CALM_S = 6.0
LOCATE_EVERY_S = 0.5
EVAL_AFTER_ACTION_S = 0.05
EVAL_AFTER_HOLD_S = 0.25
ARENA = ((-300.0, 1200.0), (-400.0, 900.0))


# ---------- signature memory: what worked against jammers the CNN did not know ----------
UNKNOWN_ORDER = ("keyed", "power_bpsk", "survival")
UNKNOWN_LABEL = {"keyed": "keyed hopping (secret sequence, avoiding busy channels)",
                 "power_bpsk": "maximum power + BPSK on the cleanest channel",
                 "survival": "survival mode (BPSK, every bit twice)"}
UNKNOWN_TRY_S = 1.5
UNKNOWN_GOOD_LOSS = 0.25
RECALL_SIM = 0.92
SIGNATURES: list[dict] = []          # shared by every AI in this server process (both worlds, every mission)


def _cos(a, b) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def recall(emb) -> tuple[Optional[dict], float]:
    best, sim = None, 0.0
    for s in SIGNATURES:
        c = _cos(emb, s["emb"])
        if c > sim:
            best, sim = s, c
    return (best, sim) if best is not None and sim >= RECALL_SIM else (None, sim)


def remember(emb, name: str, action: str, loss: float) -> None:
    for s in SIGNATURES:
        if s["name"] == name:
            s.update(action=action, loss=loss, emb=0.5 * s["emb"] + 0.5 * emb, seen=s.get("seen", 1) + 1)
            return
    SIGNATURES.append({"name": name, "emb": np.asarray(emb, float), "action": action, "loss": loss, "seen": 1})


def battery_low(w) -> Optional[str]:
    """Id of the airborne drone with the lowest battery if it is below battery.LOW_AT, else None."""
    if w is None:
        return None
    from sim.battery import LOW_AT
    low = [(n.battery, n.id) for n in w.net.nodes
           if getattr(n, "battery", None) is not None and getattr(n, "phase", "flying") == "flying" and n.battery < LOW_AT]
    return min(low)[1] if low else None


class AegisDefender(Defender):
    name = "aegis"

    def __init__(self, rng=None):
        super().__init__(rng)
        self.cnn = load_shared()
        if self.cnn is None:
            raise ImportError("CNN weights missing (ai/weights/cnn_v1.npz); run python -m ai.train")
        self.guard = HopGuard(4, 5.0, 2.0)
        self.probe = None                 # testing keyed hopping under a wideband jammer
        self.bt = BehaviourTracker()      # multi-label view: several jamming behaviours at once (ai/behaviours.py)
        self.assessment = None
        self._next_assess = 0.0
        self.track_until = -1.0           # 'tracking' (a jammer on our channel wherever we go) latched for 3 s
        self.unk: Optional[dict] = None   # an unknown jammer: which safe response is being tried
        self.events: list[tuple[str, str]] = getattr(self, "events", [])
        self._extras_cache: Optional[dict] = None
        self._extras_t = -1.0
        self.last_change_t = -10.0
        self.last_channel: Optional[int] = None
        self.last_action = ""
        self.follow_hits = 0
        self.calm_since: Optional[float] = None
        self.estimates: list[dict] = []
        self.events: list[tuple[str, str]] = []
        self._next_locate = 0.0
        self._announced = False
        self._next_eval = 0.0
        self.vote_log: list[str] = []
        self.trail: deque[tuple[int, int]] = deque(maxlen=40)    # (our channel, hottest channel or -1) per slot

    # ---------- helpers ----------
    def _hot(self, obs: SlotObs) -> list[int]:
        return [c for c in range(N_CH) if obs.energy_dbm[c] > NOISE_INBAND_DBM + HOT_DB]

    def _cleanest(self, obs: SlotObs, exclude: set[int]) -> int:
        e = obs.energy_dbm.copy()
        for c in exclude | set(obs.blacklist):
            e[c] = np.inf
        if np.all(np.isinf(e)):
            e = obs.energy_dbm.copy()
            e[obs.channel] = np.inf
        return int(np.argmin(e))

    def _sense_node(self, obs: SlotObs) -> str:
        """Receiver of the node that is losing the most packets (its parent on the route)."""
        w = obs.world
        if w is None:
            return "CP"
        worst = min((n for n in w.node_ema if n != "CP" and n in w.net.index), key=lambda n: w.node_ema[n], default=None)
        return (w.net.parent.get(worst) or "CP") if worst else "CP"

    def spectrum_extras(self, window_db: np.ndarray) -> dict:
        # display only (the defender's own decisions always use fresh windows): refresh the panel's CNN bars and
        # Grad-CAM at most 5 times a second, which halves the cost of a spectrum message on a slow laptop
        now = time.perf_counter()
        if self._extras_cache is not None and now - self._extras_t < DISPLAY_CNN_EVERY_S:
            return self._extras_cache
        self._extras_t = now
        r = self.cnn.predict(window_db, with_cam=True)
        nov = ood_ratio(r["emb"], r["k"])
        out = {"probs": {c: round(float(p), 4) for c, p in zip(CLASSES, r["probs"])},
               "verdict": "unknown" if nov > UNKNOWN_RATIO and r["class"] != "clean" else r["class"],
               "confidence": round(r["confidence"], 4), "novelty": round(nov, 2)}
        if r["class"] != "clean":
            out["gradcam"] = np.round(r["cam"], 2).tolist()
        if self.assessment is not None:
            out["behaviours"] = self.assessment.to_msg()
        if hasattr(self, "ctx"):
            out["referee"] = {"context": self.ctx, "leader": self.leader}
        self._extras_cache = out
        return out

    # ---------- main ----------
    def _copies_us(self) -> Optional[int]:
        """While we hop every slot: does the jammed channel match where we were L slots ago? A follower
        re-tunes to our old channel, so one lag lines up far more often than chance (1 in 8)."""
        tr = list(self.trail)
        hot = [i for i, (_, h) in enumerate(tr) if h >= 0]
        if len(hot) < 10:
            return None
        for lag in (1, 2, 3, 4):
            pairs = [(tr[i][1], tr[i - lag][0]) for i in hot if i >= lag]
            if len(pairs) >= 8 and sum(a == b for a, b in pairs) / len(pairs) >= 0.5:
                return lag
        return None

    def tracking(self, t: float) -> bool:
        return t < self.track_until

    def _assess(self, obs: SlotObs) -> None:
        if obs.t < self._next_assess:
            return
        self._next_assess = obs.t + ASSESS_EVERY_S
        self.assessment = self.bt.assess()
        if self.assessment.present["tracking"]:
            self.track_until = obs.t + TRACK_LATCH_S

    def _combined(self, obs: SlotObs) -> Optional[Decision]:
        """Several behaviours at once: respond to all of them together instead of to one CNN label."""
        a = self.assessment
        if a is None:
            return None
        det = set(a.detected) | ({"tracking"} if self.tracking(obs.t) else set())
        det.discard("unknown")                        # the unknown path has its own trial-and-memory logic
        if len(det) < 2:
            return None
        acts, why = [], []
        keyed = obs.hop_mode == "keyed" and getattr(obs.world, "anti_follow", False)
        cap = POWER_CAP_LOW_BATTERY_DB if battery_low(obs.world) else POWER_CAP_DB
        if "tracking" in det and not keyed:
            acts.append(Action("mode", mode="keyed", anti_follow=True))
            why.append("keyed hopping against the jammer that tracks our channel")
        if "wideband" in det:
            if obs.tx_boost_db < cap:
                acts.append(Action("power", delta_db=cap - obs.tx_boost_db))
                why.append(f"power to +{cap:.0f} dB against the wideband energy")
            if obs.modulation == "QPSK":
                acts.append(Action("rate", modulation="BPSK"))
                why.append("BPSK for 3 dB more margin")
        if "narrowband" in det and "wideband" not in det:
            hot = [c for c in self._hot(obs) if c != obs.channel and c not in obs.blacklist]
            if hot:
                acts.insert(0, Action("blacklist", channels=tuple(sorted(hot))[:4], ttl_s=3.0))
                why.append(f"blacklist channels {', '.join(map(str, sorted(hot)[:4]))}")
        if "intermittent" in det and obs.modulation == "QPSK" and not any(x.kind == "rate" for x in acts):
            acts.append(Action("rate", modulation="BPSK"))
            why.append("BPSK so bursts cost fewer packets")
        if not acts:
            return None
        names = " + ".join(f"{n} ({a.evidence[n]})" if n in a.evidence else n for n in ("wideband", "tracking", "narrowband", "intermittent") if n in det)
        self.guard.record(obs.t)
        mode_change = any(x.kind == "mode" for x in acts)
        return Decision(self.vote_log[-1] if self.vote_log else "random", 0.8, "fast_hop" if mode_change else "raise_power",
                        f"Several jamming behaviours at once: {names}. Combined response: {'; '.join(why)}.", acts, obs.channel, None)

    def on_slot(self, obs: SlotObs) -> Optional[Decision]:
        self.bt.update(obs.energy_dbm, obs.channel)
        self._assess(obs)
        e = obs.energy_dbm
        k = int(np.argmax(e))
        self.trail.append((obs.channel, k if e[k] > NOISE_INBAND_DBM + HOT_DB else -1))
        if obs.channel != self.last_channel and obs.hop_mode == "dwell":
            self.last_change_t = obs.t
        self.last_channel = obs.channel
        self._localize(obs)
        self.loss_ema = 0.93 * getattr(self, "loss_ema", obs.loss_recent) + 0.07 * obs.loss_recent
        if obs.control_pending:
            return None
        if self.unk is not None and obs.t - self.unk["t"] >= UNKNOWN_TRY_S:
            d = self._judge_unknown_try(obs)          # an 'unknown' response was being tried: did it help?
            if d is not None:
                return d
        if obs.loss_recent <= LOSS_TRIGGER:
            return self._recover(obs)
        self.calm_since = None
        d = self._combined(obs)
        if d is not None:
            self.last_action = d.action
            self._next_eval = obs.t + EVAL_AFTER_ACTION_S
            return d
        if obs.t < self._next_eval:
            return None

        node = self._sense_node(obs)
        vote = self.cnn.predict_vote([obs.sense(node) for _ in range(3)])
        cls, conf, ms = vote["class"], vote["confidence"], round(vote["ms"], 2)
        self.vote_log.append(cls)                # raw CNN verdicts, for the benchmark's accuracy column
        hot = self._hot(obs)
        unknown, oratio = is_unknown(vote["embs"], vote["ks"])
        self.bt.unknown = bool(unknown and cls != "clean")
        if unknown and cls != "clean":
            d = self._respond_unknown(obs, cls, oratio, np.mean(vote["embs"], axis=0), hot)
            self._next_eval = obs.t + (EVAL_AFTER_ACTION_S if d is not None else EVAL_AFTER_HOLD_S)
            if d is not None:
                d.decision_time_ms = ms
                self.last_action = d.action
            return d
        if cls == "clean" and obs.channel in hot:
            cls = "random"                       # energy says otherwise: treat as narrowband interference
        if len(hot) >= 6 and cls != "sweep":
            cls = "barrage"                      # 6+ of 8 channels occupied: whole-band jamming, whatever one window looked like
        d = self._respond(obs, cls, conf, hot)
        # don't re-classify every 10 ms: soon after acting, later after deciding to hold
        self._next_eval = obs.t + (EVAL_AFTER_ACTION_S if d is not None else EVAL_AFTER_HOLD_S)
        if d is not None:
            d.decision_time_ms = ms
            self.last_action = d.action
        return d

    # ---------- jammers the CNN was never trained on ----------
    def _unknown_actions(self, name: str, obs: SlotObs, hot: list[int]) -> list[Action]:
        if name == "keyed":
            acts = [Action("mode", mode="keyed", anti_follow=True)]
            if hot:
                acts.insert(0, Action("blacklist", channels=tuple(sorted(hot))[:4], ttl_s=3.0))
            return acts
        to = self._cleanest(obs, set(hot))
        acts = [Action("mode", mode="dwell", channel=to)] if obs.hop_mode != "dwell" else []
        cap = POWER_CAP_LOW_BATTERY_DB if battery_low(obs.world) else POWER_CAP_DB
        if obs.tx_boost_db < cap:
            acts.append(Action("power", delta_db=cap - obs.tx_boost_db))
        acts.append(Action("rate", modulation="BPSK_R2" if name == "survival" else "BPSK"))
        return acts

    def _try_unknown(self, obs: SlotObs, name: str, label: str, why: str, conf: float, hot: list[int]) -> Decision:
        self.unk["current"] = name
        self.unk["t"] = obs.t
        self.unk["loss_start"] = self.loss_ema
        self.guard_unknown = True
        return Decision("unknown", conf, "fast_hop" if name == "keyed" else "raise_power",
                        f"{why} Trying: {UNKNOWN_LABEL[name]} for {UNKNOWN_TRY_S:.1f} s.",
                        self._unknown_actions(name, obs, hot), obs.channel, None)

    def _respond_unknown(self, obs: SlotObs, cnn_cls: str, oratio: float, emb: np.ndarray, hot: list[int]) -> Optional[Decision]:
        """Not one of the 7 known types. Do not guess: recall a remembered pattern, or try safe generic responses
        one at a time, keep what works, and remember it for next time (a small retrieval memory)."""
        if self.unk is not None and self.unk.get("current"):
            return None                           # already trying something; judged in on_slot
        if obs.t < getattr(self, "unk_quiet_until", -1.0):
            return None                           # a response was just confirmed: give it time
        mem, sim = recall(emb)
        if mem is not None:
            self.unk = {"name": mem["name"], "emb": emb, "tried": {}, "queue": [mem["action"]], "t": obs.t, "from_memory": True}
            return self._try_unknown(obs, mem["action"], mem["name"], f"Unknown to the CNN, but it matches {mem['name']} in the signature "
                                     f"memory (similarity {sim:.2f}); last time {UNKNOWN_LABEL[mem['action']]} worked.", 1 - min(oratio / 5, 0.9), hot)
        name = f"new pattern #{len(SIGNATURES) + 1}"
        self.unk = {"name": name, "emb": emb, "tried": {}, "queue": list(UNKNOWN_ORDER), "t": obs.t, "from_memory": False}
        self.queue_pop()
        return self._try_unknown(obs, UNKNOWN_ORDER[0], name,
                                 f"Unknown jammer: not like any of the 7 known types (its CNN fingerprint is {oratio:.1f}x outside "
                                 f"the normal range; the raw CNN guess '{cnn_cls}' is not trusted). Calling it {name}.",
                                 1 - min(oratio / 5, 0.9), hot)

    def queue_pop(self):
        if self.unk and self.unk["queue"]:
            self.unk["queue"].pop(0)

    def _judge_unknown_try(self, obs: SlotObs) -> Optional[Decision]:
        u = self.unk
        cur = u.get("current")
        if cur is None:
            return None
        loss = self.loss_ema
        u["tried"][cur] = loss
        hot = self._hot(obs)
        if loss < UNKNOWN_GOOD_LOSS or (u["from_memory"] and loss < u["loss_start"]):
            remember(u["emb"], u["name"], cur, loss)
            self.unk = None
            self.unk_quiet_until = obs.t + 5.0
            self.events.append(("good", f"{u['name']}: {UNKNOWN_LABEL[cur]} works (loss {loss:.0%}). Saved to the signature "
                                        f"memory, so next time it is applied straight away."))
            return None
        if u["queue"] and not u["from_memory"]:
            nxt = u["queue"].pop(0)
            return self._try_unknown(obs, nxt, u["name"], f"{u['name']}: {UNKNOWN_LABEL[cur]} left loss at {loss:.0%}.", 0.5, hot)
        if u["from_memory"]:                        # the remembered answer no longer works: explore again
            u.update({"from_memory": False, "queue": [a for a in UNKNOWN_ORDER if a != cur], "current": None})
            nxt = u["queue"].pop(0)
            return self._try_unknown(obs, nxt, u["name"], f"{u['name']}: the remembered response stopped working (loss {loss:.0%}).", 0.5, hot)
        best = min(u["tried"], key=u["tried"].get)
        remember(u["emb"], u["name"], best, u["tried"][best])
        self.unk = None
        self.unk_quiet_until = obs.t + 5.0
        self.events.append(("info", f"{u['name']}: tried all safe responses; best was {UNKNOWN_LABEL[best]} "
                                    f"(loss {u['tried'][best]:.0%}). Saved to the signature memory."))
        if best != cur:
            return Decision("unknown", 0.5, "raise_power" if best != "keyed" else "fast_hop",
                            f"{u['name']}: going back to the best response found, {UNKNOWN_LABEL[best]}.",
                            self._unknown_actions(best, obs, hot), obs.channel, None)
        return None

    def _survival(self, obs: SlotObs, cls: str, conf: float, hot: list[int]) -> Optional[Decision]:
        """Last rung of the fallback ladder. Hopping cannot escape (the jamming is everywhere), power and BPSK
        are at their limits, keyed hopping was tried: stop changing things, halve the data rate again
        (every bit sent twice, +3 dB) and keep the link alive with critical messages only. No looping:
        from here the defender only waits for the band to clear, then steps back down one rung at a time."""
        if obs.modulation == "BPSK_R2" or getattr(self, "loss_avg", 0.0) < SURVIVAL_LOSS:
            return None
        self.survival_since = obs.t
        return Decision(cls, conf, "survival",
                        f"Fallback: {len(hot)} of 8 channels jammed and loss still {self.loss_avg:.0%} at maximum power and BPSK. "
                        "Survival mode: no more hopping, every bit sent twice (+3 dB, quarter data rate), critical messages only. "
                        "Stepping back up as soon as the band clears.", [Action("rate", modulation="BPSK_R2")])

    def _probe_under_wideband(self, obs: SlotObs, cls: str, conf: float) -> Optional[Decision]:
        """Power and rate are maxed against a sweep/barrage but loss stays high. A narrowband jammer hidden under the
        wideband energy (a follower, say) is invisible per channel, so test it: try keyed hopping for 2 s and keep it
        only if loss drops by 10 points or more."""
        t, keyed = obs.t, obs.hop_mode == "keyed"
        self.loss_avg = 0.6 * getattr(self, "loss_avg", obs.loss_recent) + 0.4 * obs.loss_recent
        p = self.probe
        if p is None:
            if not keyed and self.loss_avg > 0.45 and t - getattr(self, "last_probe_t", -99.0) > PROBE_EVERY_S:
                self.probe = {"t": t, "loss": self.loss_avg, "ch": obs.channel}
                self.last_probe_t = t
                self.guard.record(t)
                return Decision(cls, conf, "fast_hop",
                                f"CNN sees {cls} ({conf:.0%}); power and rate are at maximum but loss is still {self.loss_avg:.0%}. "
                                f"Something may be hiding under the {cls}: trying keyed hopping for {PROBE_S:.0f} s to find out.",
                                [Action("mode", mode="keyed", anti_follow=True)], obs.channel, None)
            return None                          # everything is already at maximum: hold
        if t - p["t"] < PROBE_S:
            return None
        self.probe = None
        if keyed and self.loss_avg <= p["loss"] - PROBE_GAIN:
            return Decision(cls, conf, "fast_hop",
                            f"Keyed hopping cut loss from {p['loss']:.0%} to {self.loss_avg:.0%}: a second jammer was hiding under the "
                            f"{cls}. Keeping keyed hopping and maximum power.", [], obs.channel, None)
        if keyed:
            return Decision(cls, conf, "hop",
                            f"Keyed hopping did not help (loss {self.loss_avg:.0%} vs {p['loss']:.0%}): it is just the {cls}. "
                            f"Back to holding channel {p['ch']}.", [Action("mode", mode="dwell", channel=p["ch"])], obs.channel, p["ch"])
        return None

    def _respond(self, obs: SlotObs, cls: str, conf: float, hot: list[int]) -> Optional[Decision]:
        ch, t = obs.channel, obs.t
        pct = f"{conf:.0%}"
        keyed = obs.hop_mode == "keyed"

        if cls in ("barrage", "sweep"):
            # combined attack: a narrowband jammer (follower, random, smart...) sitting on our channel on top of the
            # wideband one. Holding still would hand it the link, so hop with the secret keyed sequence as well.
            e = obs.energy_dbm
            med = float(np.median(e))
            narrow = [c for c in range(N_CH) if e[c] > med + NARROW_ON_WIDE_DB]
            if ch in narrow and obs.loss_recent > 0.3:
                acts = [Action("blacklist", channels=tuple(narrow), ttl_s=3.0)]
                if not keyed:
                    acts.insert(0, Action("mode", mode="keyed", anti_follow=True))
                if obs.tx_boost_db < POWER_CAP_DB:
                    acts.append(Action("power", delta_db=POWER_STEP_DB))
                self.guard.record(t)
                return Decision(cls, conf, "fast_hop",
                                f"CNN sees {cls} ({pct}), and a second, narrowband jammer sits on our channel {ch} "
                                f"({e[ch] - med:.0f} dB above the rest of the band): "
                                + ("switched to keyed hopping" if not keyed else "keyed hopping")
                                + f" avoiding channels {', '.join(map(str, narrow))}"
                                + (", and raised power for the wideband jammer." if obs.tx_boost_db < POWER_CAP_DB else "."), acts, ch, None)
            what = "Barrage covers the whole band" if cls == "barrage" else "Sweep crosses every channel"
            # power costs flight time: with a drone low on battery, stop at +6 dB and lean on BPSK instead
            low = battery_low(obs.world)
            cap = POWER_CAP_LOW_BATTERY_DB if low else POWER_CAP_DB
            if low and obs.tx_boost_db > cap:
                return Decision(cls, conf, "raise_power",
                                f"{low} is low on battery: power back down to +{cap:.0f} dB to save flight time "
                                f"(BPSK keeps the margin instead).", [Action("power", delta_db=cap - obs.tx_boost_db)]
                                + ([Action("rate", modulation="BPSK")] if obs.modulation == "QPSK" else []))
            if obs.tx_boost_db < cap:
                acts = [Action("power", delta_db=POWER_STEP_DB)]
                if cls == "barrage" and obs.modulation == "QPSK":
                    acts.append(Action("rate", modulation="BPSK"))
                return Decision(cls, conf, "raise_power",
                                f"CNN sees {cls} ({pct}). {what}, so hopping won't help: raised power to "
                                f"+{min(obs.tx_boost_db + POWER_STEP_DB, cap):.0f} dB"
                                + (f" (capped at +{cap:.0f} dB: {low} is low on battery)" if low else "")
                                + ""
                                + (" and dropped to BPSK." if len(acts) > 1 else " and holding the channel."), acts)
            if obs.modulation == "QPSK":
                return Decision(cls, conf, "lower_rate", f"CNN sees {cls} ({pct}). Power is at maximum: dropped QPSK to BPSK for 3 dB more margin.",
                                [Action("rate", modulation="BPSK")])
            if self.tracking(t):                 # a tracking jammer under the wideband one: keyed hopping stays
                return self._survival(obs, cls, conf, hot)
            d = self._probe_under_wideband(obs, cls, conf)
            if d is not None or self.probe is not None:
                return d
            return self._survival(obs, cls, conf, hot)

        if self.probe is not None:               # the wideband jammer is gone: the probe is over
            self.probe = None

        if cls == "clean":
            w = obs.world
            if hot:
                return None                      # energy somewhere in the band: not a blocked link, wait for a clearer look
            if w is not None and getattr(w, "desynced", None):
                return None                      # a radio is re-syncing (missed switch, clock, reboot): not a blocked link
            if w is not None and not any(n.kind == "relay" for n in w.net.nodes):
                worst = min((n for n in w.node_ema if n != "CP" and n in w.net.index), key=lambda n: w.node_ema[n])
                return Decision("clean", conf, "relay",
                                f"Loss {obs.loss_recent:.0%} but the spectrum is clean ({pct}): {worst}'s link is blocked. "
                                f"Sending a relay drone above the tallest obstacle.", [Action("relay", between=(worst, "CP"))])
            return None

        # follower: the jammer found our new channel right after a hop
        if cls in ("random", "reactive") and not keyed and self.last_action == "hop" and t - self.last_change_t < FOLLOW_WINDOW_S \
                and ch in hot:
            # re-found within 100 ms counts double: a random jammer landing on our exact new channel that
            # fast is very unlikely, a follower does it every time
            self.follow_hits += 2 if t - self.last_change_t < 0.1 else 1
        elif cls not in ("random", "reactive"):
            self.follow_hits = 0
        if self.follow_hits >= 2 and not keyed:
            self.follow_hits = 0
            self.guard.record(t)
            return Decision(cls, conf, "fast_hop",
                            f"Jammer re-found our channel {1000 * (t - self.last_change_t):.0f} ms after the hop: it is following us. "
                            f"Switched to the key-derived hop sequence, a new channel every 10 ms that it cannot predict.",
                            [Action("mode", mode="keyed", anti_follow=True)], ch, None)

        if cls == "reactive" and keyed and not getattr(obs.world, "anti_follow", False) and self._copies_us() is not None:
            cls = "random"                       # behaviour says follower; handled by the keyed branch below
        if cls == "reactive":
            acts = [Action("blacklist", channels=(ch,), ttl_s=6.0)]
            if not keyed:
                acts.insert(0, Action("mode", mode="keyed"))
            self.guard.record(t)
            return Decision(cls, conf, "fast_hop",
                            f"CNN sees reactive jamming ({pct}): it only fires when it hears us on channel {ch}. "
                            f"{'Switched to keyed fast hopping and b' if not keyed else 'B'}lacklisted channel {ch}.", acts, ch, None)

        if keyed and getattr(obs.world, "anti_follow", False) and cls != "reactive":
            if self._copies_us() is None and not self.tracking(t):
                self._no_follow_since = getattr(self, "_no_follow_since", None) or t
                if t - self._no_follow_since > 3.0:
                    self._no_follow_since = None
                    to = self._cleanest(obs, set(hot) | {ch})
                    self.guard.record(t)
                    return Decision(cls, conf, "hop", f"No sign of following for 3 s: the interference is {cls}, not a follower. "
                                    f"Back to choosing the cleanest channel ({to}).",
                                    [Action("blacklist", channels=tuple(sorted(set(hot))), ttl_s=3.0), Action("mode", mode="dwell", channel=to)],
                                    ch, to)
            else:
                self._no_follow_since = None

        if keyed and not getattr(obs.world, "anti_follow", False):
            lag = self._copies_us()
            if lag is not None:
                self.trail.clear()
                return Decision("random", conf, "fast_hop",
                                f"The jammer copies the channel we used {lag * 10} ms earlier: it is following us. "
                                f"The key-derived sequence now never reuses the last 3 channels.",
                                [Action("mode", mode="keyed", anti_follow=True)], ch, None)

        if keyed:
            # already hopping every slot: keep it off channels that stay occupied. Against a follower the
            # channels we just used are skipped (it sits there, and the sequence already avoids them).
            anti = bool(getattr(obs.world, "anti_follow", False))
            recent = (set(getattr(obs.world, "recent_channels", ())) | {ch}) if anti else set()
            bl = tuple(sorted(set(hot) - recent))
            if not bl or set(bl) <= set(obs.blacklist):
                return None                       # nothing new to blacklist: stay quiet
            return Decision(cls, conf, "fast_hop", f"CNN sees {cls} ({pct}) while fast hopping: blacklisted channels {', '.join(map(str, bl))}.",
                            [Action("blacklist", channels=bl, ttl_s=3.0)], ch, None)

        if not self.guard.allowed(t):
            if obs.loss_recent < 0.5:
                return None                       # hop guard: hold instead of thrashing
            # holding would mean losing most packets: go to keyed fast hopping off the occupied channels
            return Decision(cls, conf, "fast_hop", f"Too many hops in 5 s and still losing {obs.loss_recent:.0%}: switched to keyed fast hopping.",
                            [Action("mode", mode="keyed"), Action("blacklist", channels=tuple(sorted(set(hot) | {ch})), ttl_s=3.0)], ch, None)

        ttl = {"random": 3.0, "deceptive": 15.0, "accidental": 6.0}.get(cls, 3.0)
        bl = set(hot) | {ch} if cls == "random" else {ch}
        to = self._cleanest(obs, bl)
        self.guard.record(t)
        reasons = {
            "random": f"CNN sees random-channel jamming ({pct}) on channel{'s' if len(hot) > 1 else ''} {', '.join(map(str, sorted(hot))) or ch}. Hopped {ch} → {to}.",
            "deceptive": f"CNN sees deceptive look-alike frames ({pct}) on channel {ch}; authentication rejects them, and we hopped {ch} → {to}.",
            "accidental": f"Interference on channel {ch} looks like a friendly emitter ({pct}), not an attack. No alarm; moved quietly to channel {to}.",
        }
        return Decision(cls, conf, "hop", reasons.get(cls, f"CNN sees {cls} ({pct}). Hopped {ch} → {to}."),
                        [Action("blacklist", channels=tuple(sorted(bl)), ttl_s=ttl), Action("hop", channel=to)], ch, to)

    def _recover(self, obs: SlotObs) -> Optional[Decision]:
        """Quiet band for a while: undo the emergency measures one step at a time."""
        busy = bool(self._hot(obs))
        if obs.modulation == "BPSK_R2" and getattr(self, "loss_avg", 1.0) < 0.05 and not busy:
            busy = False                              # survival mode ends on a clean band like any other rung
        if busy or (obs.tx_boost_db == 0 and obs.modulation == "QPSK" and obs.hop_mode != "keyed"):
            self.calm_since = None if busy else obs.t
            return None
        if self.calm_since is None:
            self.calm_since = obs.t
            return None
        if obs.t - self.calm_since < CALM_S:
            return None
        self.calm_since = obs.t
        if obs.hop_mode == "keyed":
            to = self._cleanest(obs, set())
            return Decision("clean", 0.99, "hop", f"Band quiet for {CALM_S:.0f} s: leaving fast-hop mode, settling on channel {to}.",
                            [Action("mode", mode="dwell", channel=to)], obs.channel, to)
        if obs.modulation == "BPSK_R2":
            return Decision("clean", 0.99, "lower_rate", "Band quiet: leaving survival mode, back to BPSK (double the data rate).",
                            [Action("rate", modulation="BPSK")])
        if obs.modulation == "BPSK":
            return Decision("clean", 0.99, "lower_rate", "Band quiet: back to QPSK (full data rate).", [Action("rate", modulation="QPSK")])
        return Decision("clean", 0.99, "raise_power", f"Band quiet: power back down to +{max(obs.tx_boost_db - POWER_STEP_DB, 0):.0f} dB.",
                        [Action("power", delta_db=-POWER_STEP_DB)])

    def _localize(self, obs: SlotObs) -> None:
        if obs.t < self._next_locate or obs.world is None:
            return
        self._next_locate = obs.t + LOCATE_EVERY_S
        pos, rssi, exps = obs.world.rssi_reports()
        if len(pos) < 4:                          # fewer than 4 nodes hear anything hostile
            self.estimates = []
            self._announced = False
            return
        est = locate(pos, rssi, ARENA, exps)
        if est is None or est.radius_m > PUBLISH_RADIUS_M:
            return
        x, y = float(np.clip(est.x, *ARENA[0])), float(np.clip(est.y, *ARENA[1]))
        # fuse with earlier estimates while they agree (a ground jammer stays put between readings): inverse-variance
        # weighted mean, radius shrinking as 1/sqrt(weight). A clearly different estimate restarts the track
        # (the jammer moved, or a new one appeared).
        f = getattr(self, "_fused", None)
        w_new = 1.0 / est.radius_m ** 2
        if f is not None and np.hypot(x - f["x"], y - f["y"]) <= 2.0 * max(f["r"], est.radius_m):
            W = f["w"] + w_new
            f = {"x": (f["x"] * f["w"] + x * w_new) / W, "y": (f["y"] * f["w"] + y * w_new) / W, "w": W, "n": f["n"] + 1}
        else:
            f = {"x": x, "y": y, "w": w_new, "n": 1}
        # readings 0.5 s apart are not independent (same geometry, slow fading): count at most 1 in 3 as new information
        f["r"] = 1.0 / np.sqrt(f["w"] / (1 + (f["n"] - 1) * 2 / 3) * 1.0) if f["n"] > 1 else est.radius_m
        # averaging removes scatter, not bias (a hill between the jammer and several radios biases every reading the
        # same way), so the fused radius never drops below 3/4 of a single reading's
        f["r"] = max(f["r"], est.radius_m / np.sqrt(1 + (f["n"] - 1) / 3), 0.75 * est.radius_m)
        self._fused = f
        x, y = f["x"], f["y"]
        self.estimates = [{"pos": [round(x, 1), round(y, 1)], "radius_m": round(f["r"], 1), "readings": f["n"]}]
        nearest = obs.world.nearest_jammer_id(x, y)
        if nearest:
            self.estimates[0]["jammer_id"] = nearest
        if est.radius_m < ANNOUNCE_RADIUS_M and not self._announced:
            self._announced = True
            self.events.append(("info", f"Jammer located near ({x:.0f}, {y:.0f}) m, ±{est.radius_m:.0f} m"
                                        f" ({est.used} nodes{f', {est.dropped} blocked reading dropped' if est.dropped else ''})."))


# ---------------------------------------------------------------------------------------------- hybrid
JUDGE_S = 6.0                 # the referee looks at delivery over this window (attacks come in bursts)
TRIAL_BELOW = 0.93            # below this delivery over JUDGE_S, the other controller gets a trial
TRIAL_S = 4.0                 # length of a trial
TRIAL_EVERY_S = 12.0          # at most one trial this often
TRIAL_MARGIN = 0.03           # the challenger must beat the incumbent's delivery by this much to take over
STUCK_LOSS = 0.5              # clearly stuck: hand over at once (no waiting for the trial timer)
STUCK_S = 2.0


class HybridDefender(AegisDefender):
    """AEGIS (CNN + rules) and the RL policy, with a context-aware referee.

    Both controllers run every slot; the referee decides whose decisions reach the radio. It looks at:
      * the kind of attack, from the multi-label assessment (ai/behaviours.py):
          stacked   - two or more behaviours at once (e.g. wideband + tracking), kept for 10 s after the last
                      sighting. AEGIS leads: it has the combined response, the RL agent does not.
          volatile  - the behaviour keeps changing (>= 0.4 changes a second over 5 s) with no wideband part:
                      a jammer switching tactics. No default: both controllers are judged on short 2 s windows
                      and the scoreboard keeps whichever delivers more in this kind of attack.
          steady    - one stable behaviour: AEGIS leads, the RL policy gets a trial if delivery stays low.
      * a scoreboard of how each controller actually did in each kind of attack this mission
        (previous countermeasure effectiveness): it overrides the default when the evidence is clear;
      * shorter judging and trial windows when the attack is volatile (2 s instead of 6 s / 4 s).
    Every action from either controller still goes through the same safety guard.
    """
    name = "hybrid"

    def __init__(self, rng=None):
        super().__init__(rng)
        from ai.rl import RLDefender
        self.rl = RLDefender(rng)
        self.leader = "aegis"
        self.high_since: Optional[float] = None
        self.trial: Optional[dict] = None
        self.last_trial_t = -99.0
        self.win: deque[tuple[float, int, int]] = deque()       # (t, sent, delivered) over the last JUDGE_S
        self.ctx = "steady"
        self.ctx_hist: deque[tuple[float, tuple]] = deque()     # (t, detected behaviours) for volatility
        self._next_ctx = 0.0
        self.score: dict[tuple[str, str], float] = {}           # (context, controller) -> delivery EMA
        self.score_n: dict[tuple[str, str], int] = {}
        self._led_since = 0.0
        self._next_score = 1.0

    def _delivery(self, t: float, span: float) -> float:
        # the window is in time order: walk back from the newest slot and stop at the cutoff (same sums, far less work)
        s = d = 0
        for tt, a, b in reversed(self.win):
            if t - tt > span:
                break
            s += a
            d += b
        return d / s if s else 1.0

    # ---- context ----
    def _context(self, t: float) -> str:
        """volatile (behaviour keeps changing, no wideband part) is checked first: one tactic-switching jammer can
        show two behaviours at a time, but never the same pair for long. stacked = a stable combination."""
        a = self.assessment
        if a is None:
            return "steady"
        det = set(a.detected) - {"unknown"}
        if self.tracking(t):
            det.add("tracking")
        self.ctx_hist.append((t, tuple(sorted(a.detected))))
        while self.ctx_hist and t - self.ctx_hist[0][0] > VOLATILE_SPAN_S:
            self.ctx_hist.popleft()
        sets = [x for _, x in self.ctx_hist]
        changes = sum(p != q for p, q in zip(sets, sets[1:]))
        span = max(self.ctx_hist[-1][0] - self.ctx_hist[0][0], 1e-6) if len(self.ctx_hist) > 1 else 1.0
        wide = sum("wideband" in x for x in sets) / max(len(sets), 1)
        if span >= VOLATILE_SPAN_S - 0.6 and changes / span >= VOLATILE_RATE and wide < 0.3:
            self._stacked_until = -1.0
            return "volatile"
        if len(det) >= 2:
            self._stacked_until = t + STACKED_LATCH_S
            return "stacked"
        if t < getattr(self, "_stacked_until", -1.0):
            return "stacked"
        return "steady"

    def _scored(self, ctx: str, who: str) -> Optional[float]:
        return self.score.get((ctx, who)) if self.score_n.get((ctx, who), 0) >= SCORE_MIN_N else None

    def _switch(self, t: float, who: str, why: str) -> None:
        if who != self.leader:
            self.leader, self._led_since, self.trial = who, t, None
            self.events.append(("info", f"Hybrid referee: {why} {LEAD[who]} takes control."))

    def on_slot(self, obs: SlotObs) -> Optional[Decision]:
        t = obs.t
        self.win.append((t, obs.sent, obs.delivered))
        while self.win and t - self.win[0][0] > JUDGE_S + 0.5:
            self.win.popleft()
        rl_d = self.rl.on_slot(obs)                  # the RL agent keeps its own view of the world up to date
        aegis_d = super().on_slot(obs)               # so does AEGIS (assessment, localisation, follower tracking, memory)
        self.loss_ema_h = 0.93 * getattr(self, "loss_ema_h", obs.loss_recent) + 0.07 * obs.loss_recent

        # scoreboard: how the current leader does in the current kind of attack (skip the first second after a switch)
        if t >= self._next_score:
            self._next_score = t + 1.0
            if t - self._led_since >= 1.0 and self.loss_ema_h > 0.02:
                k = (self.ctx, self.leader)
                got = self._delivery(t, 1.0)
                self.score[k] = got if k not in self.score else 0.7 * self.score[k] + 0.3 * got
                self.score_n[k] = self.score_n.get(k, 0) + 1

        if t >= self._next_ctx:
            self._next_ctx = t + 0.5
            ctx = self._context(t)
            if ctx != self.ctx:
                self.ctx = ctx
                a, r = self._scored(ctx, "aegis"), self._scored(ctx, "rl")
                if a is not None and r is not None and abs(a - r) >= SCORE_MARGIN:
                    best = "aegis" if a > r else "rl"
                    self._switch(t, best, f"this kind of attack ({ctx}) was seen before this mission: {LEAD[best]} delivered "
                                          f"{max(a, r):.0%} vs {min(a, r):.0%}.")
                elif ctx == "stacked":
                    self._switch(t, "aegis", "several jamming behaviours at once; only AEGIS has a combined response.")
                elif ctx == "volatile":
                    self.events.append(("info", "Hybrid referee: the attack keeps changing its behaviour (no wideband part), so labels go "
                                                "out of date fast: judging both controllers on 2 s windows and keeping whichever delivers more."))
        volatile = self.ctx == "volatile"
        judge_s, trial_s = (VOLATILE_JUDGE_S, VOLATILE_TRIAL_S) if volatile else (JUDGE_S, TRIAL_S)

        if self.trial is not None:
            if t - self.trial["t"] >= trial_s:
                challenger, incumbent = self.trial["who"], self.trial["from"]
                got = self._delivery(t, trial_s)
                before = self.trial["before"]
                if got >= before + TRIAL_MARGIN:
                    self.events.append(("good", f"Hybrid referee: {LEAD[challenger]} delivered {got:.0%} vs {before:.0%} for "
                                                f"{LEAD[incumbent]}: it keeps control."))
                else:
                    self.leader = incumbent
                    self._led_since = t
                    self.events.append(("info", f"Hybrid referee: {LEAD[challenger]} delivered {got:.0%} vs {before:.0%}: "
                                                f"{LEAD[incumbent]} takes control back."))
                self.trial = None
        elif self.ctx != "stacked":                  # stacked: AEGIS stays unless the scoreboard says otherwise
            self.high_since = (self.high_since or t) if self.loss_ema_h > STUCK_LOSS else None
            stuck = self.high_since is not None and t - self.high_since > STUCK_S
            full = bool(self.win) and t - self.win[0][0] >= judge_s - 0.1
            every = TRIAL_EVERY_S / 2 if volatile else TRIAL_EVERY_S
            due = full and self._delivery(t, judge_s) < TRIAL_BELOW and t - self.last_trial_t > every
            other = "rl" if self.leader == "aegis" else "aegis"
            known_worse = (self._scored(self.ctx, other) is not None and self._scored(self.ctx, self.leader) is not None
                           and self._scored(self.ctx, other) < self._scored(self.ctx, self.leader) - SCORE_MARGIN)
            if ((stuck and t - self.last_trial_t > STUCK_S * 2) or due) and not known_worse:
                self.trial = {"t": t, "who": other, "from": self.leader, "before": self._delivery(t, judge_s)}
                self.last_trial_t, self.high_since = t, None
                self.events.append(("info", f"Hybrid referee: {LEAD[self.leader]} is at {self.trial['before']:.0%} delivered; "
                                            f"giving {LEAD[other]} a {trial_s:.0f} s trial."))
                self.leader = other
                self._led_since = t
        d = rl_d if self.leader == "rl" else aegis_d
        if d is not None and self.leader == "rl":
            d.reason = "Hybrid → RL policy: " + d.reason
        return d


LEAD = {"aegis": "AEGIS (CNN + rules)", "rl": "the RL policy"}
