"""How well does the jammer classifier hold up on spectra it was never trained on?

The CNN is trained on simulated spectrogram windows. This test draws fresh windows (a seed never used in training),
then degrades them the way a real receiver could, none of which appears in training:

  clean            fresh windows, same generator (the in-distribution baseline)
  noisy_6db/12db   the receiver's noise floor raised by 6 / 12 dB (cheap front end, hot environment)
  tuning_1/3bins   the whole spectrum shifted by 1 / 3 FFT bins (0.625 / 1.9 MHz of local-oscillator error,
                   about 260 / 780 ppm: far worse than a TCXO radio's ~2 ppm, so an extreme case)
  gain_+-6db       every bin 6 dB too high or too low (automatic gain control mis-set)
  dropped_frames   3 of the 12 time rows lost and replaced by the noise floor (USB / DMA overruns). The receiver
                   reports which frames it dropped, and the calibrated pipeline fills them from neighbouring frames.

    python -m ai.stress_test            -> prints a table, writes docs/cnn_stress_results.json

Each condition is scored twice: raw (windows straight into the CNN) and with the start-up calibration the system
uses on a real receiver (ai/calibration.py): one second of a quiet band through the same degraded receiver sets the
gain and floor, then every window is corrected before the CNN sees it. It reports accuracy per condition and per
class, so a weak spot is visible rather than averaged away.
Simulated windows only: this measures robustness to these distortions, not accuracy on real captures.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from ai.calibration import Calibrator
from ai.cnn import CNN
from ai.dataset import synthetic_window
from ai.features import CLASSES
from sim.phy import NumpyPhy

OUT = Path(__file__).resolve().parent.parent / "docs" / "cnn_stress_results.json"
SEED = 9001          # training used other seeds; these windows are new draws


def _add_noise(w: np.ndarray, rise_db: float, rng) -> np.ndarray:
    floor = np.percentile(w, 20)                                   # robust estimate of the noise floor (dBm per bin)
    extra = 10 ** ((floor + rise_db) / 10) * rng.exponential(1.0, w.shape)
    return 10 * np.log10(10 ** (w / 10) + extra)


LAST_DROPPED: list[int] = []      # what the receiver's overflow flag would report for the last window


def _drop_rows(w: np.ndarray, k: int, rng) -> np.ndarray:
    w = w.copy()
    floor = np.percentile(w, 20)
    rows = rng.choice(w.shape[0], size=k, replace=False)
    LAST_DROPPED[:] = [int(r) for r in rows]
    for r in rows:
        w[r] = floor + rng.normal(0, 1.0, w.shape[1])
    return w


CONDITIONS = {
    "clean": lambda w, rng: w,
    "noisy_6db": lambda w, rng: _add_noise(w, 6.0, rng),
    "noisy_12db": lambda w, rng: _add_noise(w, 12.0, rng),
    "tuning_1bin": lambda w, rng: np.roll(w, 1, axis=1),
    "tuning_3bins": lambda w, rng: np.roll(w, 3, axis=1),
    "gain_+6db": lambda w, rng: w + 6.0,
    "gain_-6db": lambda w, rng: w - 6.0,
    "dropped_frames": lambda w, rng: _drop_rows(w, 3, rng),
}


def run(n_per_class: int = 80, seed: int = SEED) -> dict:
    cnn = CNN()
    phy = NumpyPhy()
    rng = np.random.default_rng(seed)
    windows = [(k, synthetic_window(cls, rng, phy)) for k, cls in enumerate(CLASSES) for _ in range(n_per_class)]
    res = {"seed": seed, "windows": len(windows), "classes": CLASSES, "conditions": {},
           "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    qrng = np.random.default_rng(seed + 7)
    quiet = [synthetic_window("clean", qrng, phy) for _ in range(12)]     # the start-up capture of a quiet band
    for name, f in CONDITIONS.items():
        entry = {}
        for mode in ("raw", "calibrated"):
            prng = np.random.default_rng(seed + 1)
            cal = None
            if mode == "calibrated":
                cal = Calibrator()
                cal.calibrate_quiet([f(q, prng) for q in quiet])
            hit = np.zeros(len(CLASSES))
            conf = np.zeros((len(CLASSES), len(CLASSES)), int)
            for k, w in windows:
                LAST_DROPPED.clear()
                x = f(w, prng)
                if cal is not None:
                    cal.drift = 0.0                    # every test window is independent
                    x = cal.calibrate(x, missing_rows=list(LAST_DROPPED))
                p = int(np.argmax(cnn.predict(x)["probs"]))
                conf[k, p] += 1
                hit[k] += p == k
            per = {c: round(100 * hit[i] / n_per_class, 1) for i, c in enumerate(CLASSES)}
            entry[mode] = {"accuracy_pct": round(100 * hit.sum() / len(windows), 1), "per_class_pct": per,
                           "worst_class": min(per, key=per.get), "confusion": conf.tolist()}
        res["conditions"][name] = entry
    return res


def main() -> None:
    r = run()
    OUT.write_text(json.dumps(r, indent=2))
    print(f"{'condition':16s} {'raw':>7s} {'calibrated':>11s}   worst class (calibrated)")
    for name, c in r["conditions"].items():
        cc = c["calibrated"]
        print(f"{name:16s} {c['raw']['accuracy_pct']:6.1f} % {cc['accuracy_pct']:9.1f} %   "
              f"{cc['worst_class']} ({cc['per_class_pct'][cc['worst_class']]} %)")
    print(f"\n{r['windows']} fresh windows per condition -> {OUT}")


if __name__ == "__main__":
    main()
