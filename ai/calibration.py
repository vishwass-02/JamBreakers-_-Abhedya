"""RF measurement calibration: turn what a real receiver reports into what the CNN was trained on.

Channel plan (what "a channel" means physically)
  8 channels, centres 2410, 2420, ... 2480 MHz (10 MHz spacing). Our signal is QPSK at 2 Msymbol/s with a
  root-raised-cosine filter (roll-off 0.35), so it occupies 2.7 MHz; the receiver's channel filter is the
  matched RRC filter. The sensing FFT covers 2405-2485 MHz in 128 bins of 625 kHz; channel centres fall on
  bin edges, so each channel's occupied band is measured over the 4 bins whose centres lie inside it
  (2.5 MHz of the 2.7 MHz).

What a real receiver gets wrong, and what this module does about it
  unknown gain       antenna gain, cable loss and LNA gain shift every reading by a constant. Measured ONCE, at
                     start-up, on a quiet band (or with a reference noise source): calibrate_quiet().
                     Not tracked from the live spectrum: from the spectrum alone an unknown gain change and a
                     jammer that lifts the whole band look identical, so a self-adjusting floor would
                     calibrate a barrage away (the first version of this module did exactly that in testing).
  AGC steps          the radio knows its own gain setting: pass it as rx_gain_db and it is removed exactly.
                     (For sensing, a fixed manual gain is the usual practice; SDRs report the gain they use.)
  ripple / tilt      the antenna and filter response across 80 MHz, measured with the start-up calibration and
                     removed, so a slope is not mistaken for a wideband jammer.
  DC spike           direct-conversion receivers show a spike at the centre frequency; those bins are
                     replaced by their neighbours.
  dropped frames     an SDR that cannot keep up drops FFT frames and says so (UHD / SoapySDR overflow flags). A lost
                     frame must not look like a jammer switching off: pass its row numbers as missing_rows and
                     it is filled from the nearest frame that did arrive. Without this, a lost frame reads as the
                     on/off pattern of a random jammer (stress test: 'random' accuracy 2.5 % -> see docs/EVIDENCE.md).
  rising background  other devices lift the floor slowly; the floor follows it only through bins that are
                     quiet, at most 0.5 dB a second, so a jammer that switches on is never absorbed into it.
"""
from __future__ import annotations


import numpy as np

from backend.protocol import CHANNELS_MHZ, FFT_BINS, F_START_MHZ, F_STOP_MHZ

from .features import NOISE_BIN_DBM

BIN_MHZ = (F_STOP_MHZ - F_START_MHZ) / FFT_BINS
OCCUPIED_MHZ = 2.7
CH_BINS = [np.flatnonzero(np.abs(F_START_MHZ + BIN_MHZ * (np.arange(FFT_BINS) + 0.5) - c) <= OCCUPIED_MHZ / 2)
           for c in CHANNELS_MHZ]
DC_BINS = (FFT_BINS // 2 - 1, FFT_BINS // 2)
MAX_DRIFT_DB_S = 0.5
OCCUPIED_DB = 6.0


class Calibrator:
    def __init__(self) -> None:
        self.gain = 0.0                       # dB to subtract (start-up calibration)
        self.shape = np.zeros(FFT_BINS)       # ripple / tilt across the band (start-up calibration)
        self.drift = 0.0                      # slow background change since calibration (dB)
        self.calibrated = False

    @staticmethod
    def _dc(w: np.ndarray) -> np.ndarray:
        w = w.copy()
        a, b = DC_BINS
        w[:, a:b + 1] = 0.5 * (w[:, a - 1:a] + w[:, b + 1:b + 2])
        return w

    def calibrate_quiet(self, windows: list[np.ndarray], rx_gain_db: float = 0.0) -> dict:
        """Start-up: a second or so of a quiet band (or a reference noise source at the input)."""
        w = np.concatenate([self._dc(np.asarray(x, np.float64)) for x in windows]) - rx_gain_db
        floor_bins = np.median(w, axis=0)
        k = 9
        smooth = np.convolve(np.pad(floor_bins, k // 2, mode="edge"), np.ones(k) / k, mode="valid")
        level = float(np.median(smooth))
        self.shape = smooth - level
        self.gain = level - NOISE_BIN_DBM
        self.drift, self.calibrated = 0.0, True
        return {"gain_db": round(self.gain, 1), "ripple_pp_db": round(float(np.ptp(self.shape)), 1)}

    @staticmethod
    def fill_missing(w: np.ndarray, missing_rows) -> np.ndarray:
        """Rows the receiver reported as dropped take the value of the nearest row that arrived."""
        bad = sorted(set(int(r) for r in missing_rows if 0 <= int(r) < w.shape[0]))
        good = [r for r in range(w.shape[0]) if r not in bad]
        if not bad or not good:
            return w
        w = w.copy()
        for r in bad:
            w[r] = w[min(good, key=lambda g: (abs(g - r), g))]
        return w

    def calibrate(self, raw_window: np.ndarray, rx_gain_db: float = 0.0, dt: float = 0.02, missing_rows=()) -> np.ndarray:
        w = self._dc(np.asarray(raw_window, dtype=np.float64)) - rx_gain_db - self.shape - self.gain
        w = self.fill_missing(w, missing_rows)
        # slow background tracking through quiet bins only (rate limited)
        quiet = w[w < NOISE_BIN_DBM + self.drift + 3.0]
        if quiet.size > w.size * 0.2:
            err = float(np.median(quiet)) - (NOISE_BIN_DBM + self.drift)
            self.drift += float(np.clip(err, -MAX_DRIFT_DB_S * dt, MAX_DRIFT_DB_S * dt))
        return (w - self.drift).astype(np.float32)

    @staticmethod
    def occupancy(window: np.ndarray) -> list[dict]:
        """Per channel, over its occupied 2.7 MHz: mean level above the floor, and whether it is occupied."""
        lin = 10 ** (np.asarray(window) / 10)
        out = []
        for ch, bins in enumerate(CH_BINS):
            p = 10 * np.log10(lin[:, bins].mean())
            margin = p - NOISE_BIN_DBM
            out.append({"channel": ch, "margin_db": round(float(margin), 1), "occupied": bool(margin > OCCUPIED_DB)})
        return out


# ------------------------------------------------------------------ test impairments
class Impairments:
    """What a real front end does to the measurement (for testing the calibrator on simulated windows)."""

    def __init__(self, seed: int = 0, gain_db: float | None = None, ripple_db: float = 3.0, dc_db: float = 25.0,
                 agc_every: int = 40, agc_step_db: float = 8.0, background_db: float = 4.0):
        rng = np.random.default_rng(seed)
        self.rng = rng
        self.gain = float(rng.uniform(-15, 15)) if gain_db is None else gain_db
        x = np.linspace(-1, 1, FFT_BINS)
        self.ripple = ripple_db * (0.6 * x + 0.4 * np.sin(3 * np.pi * x + rng.uniform(0, 6.28)))
        self.dc = dc_db
        self.agc_every, self.agc_step = agc_every, agc_step_db
        self.background = background_db
        self.n = 0
        self.agc = 0.0

    @property
    def reported_gain_db(self) -> float:
        """What the radio reports as its current AGC gain change."""
        return self.agc

    def apply(self, window: np.ndarray) -> np.ndarray:
        self.n += 1
        if self.agc_every and self.n % self.agc_every == 0:
            self.agc = -self.agc_step if self.agc == 0 else 0.0
        w = np.asarray(window, dtype=np.float64)
        floor_lin = 10 ** (NOISE_BIN_DBM / 10) * (10 ** (self.background / 10) - 1)    # extra background power
        w = 10 * np.log10(10 ** (w / 10) + floor_lin)
        w = w + self.gain + self.agc + self.ripple
        a, b = DC_BINS
        w[:, a:b + 1] = np.maximum(w[:, a:b + 1], NOISE_BIN_DBM + self.gain + self.agc + self.dc)
        return w.astype(np.float32)
