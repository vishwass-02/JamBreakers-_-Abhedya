"""Where the AI's spectrum comes from: the simulator, a recorded file, or a live radio.

The CNN and the open-set ("unknown") detector only ever see a (12 x 128) window of dBm values over
2405-2485 MHz. Anything that can produce such windows can feed the AI:

  * simulator   - SimWorld.sense(), what the live arena uses (no change);
  * recording   - a .npz file in recordings/: measured spectrum from a real receiver (a Wi-Fi card's spectral
                  scan, a HackRF sweep, a public dataset), resampled onto our 128 bins and floor-aligned;
  * live radio  - hackrf_sweep streaming from a HackRF One (needs the device and HackRF tools installed).

A recording replays through the classifier only: it is a fixed capture, so the AI's counter-moves cannot
change what happens next. The closed-loop AI OFF vs AI ON comparison stays in the simulator, where every
defence can face the identical attack.

Recording file (.npz):
  psd_dbm   (N, B) float   power per frequency bin, one row per scan
  freqs_mhz (B,)   float   centre frequency of each bin
  t_s       (N,)   float   time of each row (optional)
  labels    (N,)   str     what was on the air for each row, if known (optional)
  meta      ()     str     JSON: name, source, licence, note

CLI:
  python -m ai.sources convert-sweep  in.csv  --out recordings/x.npz [--label barrage] [--source "..."]
  python -m ai.sources convert-wide   in.csv  --out recordings/x.npz [--label ...]   (time, then one column per MHz)
  python -m ai.sources capture --jammer sweep --seconds 4 --out recordings/sim_sweep.npz
  python -m ai.sources eval recordings/x.npz
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Iterator, Optional

import numpy as np

from backend.protocol import FFT_BINS, F_START_MHZ, F_STOP_MHZ

from .features import NOISE_BIN_DBM

ROOT = Path(__file__).resolve().parents[1]
RECORDINGS = ROOT / "recordings"
ROWS = 12                                       # window height the CNN expects
BIN_MHZ = (F_STOP_MHZ - F_START_MHZ) / FFT_BINS
BIN_CENTRES = F_START_MHZ + BIN_MHZ * (np.arange(FFT_BINS) + 0.5)


# ------------------------------------------------------------------ recordings
class Recording:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        d = np.load(self.path, allow_pickle=False)
        self.psd = np.asarray(d["psd_dbm"], dtype=np.float32)
        self.freqs = np.asarray(d["freqs_mhz"], dtype=np.float64)
        self.t = np.asarray(d["t_s"], dtype=np.float64) if "t_s" in d else np.arange(len(self.psd), dtype=np.float64)
        self.labels = [str(x) for x in d["labels"]] if "labels" in d else None
        self.meta = json.loads(str(d["meta"])) if "meta" in d else {}
        self.name = self.meta.get("name", self.path.stem)
        if self.psd.ndim != 2 or self.psd.shape[1] != len(self.freqs):
            raise ValueError(f"{self.path.name}: psd_dbm must be (rows, bins) matching freqs_mhz")
        if len(self.psd) < ROWS:
            raise ValueError(f"{self.path.name}: needs at least {ROWS} rows, has {len(self.psd)}")
        # the floor reference: rows labelled clean, or a floor level stated in the metadata; only as a last resort
        # the recording itself (then a recording jammed from start to finish looks quieter than it was)
        quiet = np.array([lab == "clean" for lab in self.labels]) if self.labels else None
        ref = self.meta.get("floor_dbm")
        self.floor_source = ("stated floor" if ref is not None else "clean rows" if quiet is not None and quiet.sum() >= ROWS
                             else "estimated from the recording itself")
        self.rows = to_our_bins(self.psd, self.freqs, quiet_rows=quiet if self.floor_source == "clean rows" else None,
                                floor_dbm=ref)

    @property
    def n_windows(self) -> int:
        return (len(self.rows) - ROWS) // (ROWS // 2) + 1

    def window(self, k: int) -> tuple[np.ndarray, Optional[str], float]:
        """k-th window (half-overlapping), its majority label, and its start time."""
        a = (k % self.n_windows) * (ROWS // 2)
        lab = None
        if self.labels:
            lab = Counter(self.labels[a:a + ROWS]).most_common(1)[0][0]
        return self.rows[a:a + ROWS], lab, float(self.t[a])

    def windows(self) -> Iterator[tuple[np.ndarray, Optional[str], float]]:
        for k in range(self.n_windows):
            yield self.window(k)

    def info(self) -> dict:
        return {"file": self.path.name, "name": self.name, "rows": len(self.rows), "windows": self.n_windows,
                "labelled": self.labels is not None, "source": self.meta.get("source", "unknown"),
                "licence": self.meta.get("licence"), "note": self.meta.get("note"),
                "simulated": bool(self.meta.get("simulated", False)), "floor_reference": self.floor_source,
                "freq_mhz": [round(float(self.freqs.min()), 1), round(float(self.freqs.max()), 1)]}


def to_our_bins(psd: np.ndarray, freqs_mhz: np.ndarray, quiet_rows: Optional[np.ndarray] = None,
                floor_dbm: Optional[float] = None) -> np.ndarray:
    """Resample measured rows onto our 128 bins (2405-2485 MHz) and align the noise floor.

    Different receivers report different absolute levels (a Wi-Fi card's spectral scan is not calibrated
    like a HackRF), so the recording's own floor (10th percentile) is shifted onto our receiver's floor.
    Bins outside the recorded range get the floor: nothing measured there."""
    order = np.argsort(freqs_mhz)
    f, p = freqs_mhz[order], psd[:, order]
    if floor_dbm is not None:
        floor = float(floor_dbm)
    elif quiet_rows is not None:
        floor = float(np.median(p[np.asarray(quiet_rows, bool)]))
    else:
        floor = float(np.percentile(p, 10))
    p = p - floor + NOISE_BIN_DBM
    out = np.full((len(p), FFT_BINS), NOISE_BIN_DBM, dtype=np.float32)
    inside = (BIN_CENTRES >= f[0]) & (BIN_CENTRES <= f[-1])
    for i, row in enumerate(p):
        # average in linear power over each of our bins when the source is finer, interpolate when coarser
        out[i, inside] = np.interp(BIN_CENTRES[inside], f, row)
    return out


def save_recording(path: Path | str, psd: np.ndarray, freqs: np.ndarray, t: Optional[np.ndarray] = None,
                   labels: Optional[list[str]] = None, **meta) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arrays = {"psd_dbm": np.asarray(psd, np.float32), "freqs_mhz": np.asarray(freqs, np.float64),
              "meta": np.array(json.dumps({"name": meta.pop("name", path.stem), **meta}))}
    if t is not None:
        arrays["t_s"] = np.asarray(t, np.float64)
    if labels is not None:
        arrays["labels"] = np.array([str(x) for x in labels])
    np.savez_compressed(path, **arrays)
    return path


def list_recordings() -> list[dict]:
    out = []
    for p in sorted(RECORDINGS.glob("*.npz")):
        try:
            out.append(Recording(p).info())
        except Exception as e:                          # a broken file is listed with its problem
            out.append({"file": p.name, "error": str(e)})
    return out


# ------------------------------------------------------------------ converters
def parse_hackrf_sweep(lines) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """hackrf_sweep CSV: date, time, hz_low, hz_high, hz_bin_width, num_samples, dB, dB, ...
    One line covers part of the band; lines with the same timestamp make one full sweep (one row)."""
    sweeps: dict[str, dict[float, float]] = {}
    order: list[str] = []
    for line in lines:
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 7:
            continue
        try:
            lo, width = float(parts[2]), float(parts[4])
            vals = [float(x) for x in parts[6:]]
        except ValueError:
            continue                                     # header or a broken line
        stamp = f"{parts[0]} {parts[1]}"
        if stamp not in sweeps:
            sweeps[stamp] = {}
            order.append(stamp)
        for k, v in enumerate(vals):
            sweeps[stamp][(lo + width * (k + 0.5)) / 1e6] = v
    if not order:
        raise ValueError("no hackrf_sweep lines found")
    freqs = np.array(sorted({f for s in sweeps.values() for f in s}))
    psd = np.array([[sweeps[s].get(f, np.nan) for f in freqs] for s in order], dtype=np.float32)
    psd = np.where(np.isnan(psd), np.nanmin(psd), psd)
    t = np.arange(len(order), dtype=np.float64)
    return psd, freqs, t


def parse_wide_csv(path: Path | str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A plain table: first column time in seconds, then one column per frequency, headed in MHz."""
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    freqs = np.array([float(h) for h in rows[0][1:]])
    data = np.array([[float(x) for x in r] for r in rows[1:] if r], dtype=np.float64)
    return data[:, 1:].astype(np.float32), freqs, data[:, 0]


# ------------------------------------------------------------------ live radio
class LiveHackRF:
    """Streams hackrf_sweep from a HackRF One, one full sweep per row. Needs the device plugged in and the
    HackRF tools on PATH; rows come at the sweep rate (a few per second over 80 MHz), slower than our
    simulated receiver, which is why the replay path aligns windows by rows, not by time."""

    def __init__(self, f_lo_mhz: int = 2400, f_hi_mhz: int = 2490, bin_hz: int = int(BIN_MHZ * 1e6)):
        exe = shutil.which("hackrf_sweep")
        if exe is None:
            raise RuntimeError("hackrf_sweep not found: install the HackRF tools and plug in a HackRF One")
        self.proc = subprocess.Popen([exe, "-f", f"{f_lo_mhz}:{f_hi_mhz}", "-w", str(bin_hz)],
                                     stdout=subprocess.PIPE, text=True)

    def rows(self) -> Iterator[np.ndarray]:
        buf, stamp = [], None
        for line in self.proc.stdout:                    # type: ignore[union-attr]
            s = ",".join(line.split(",")[:2])
            if stamp is not None and s != stamp and buf:
                psd, freqs, _ = parse_hackrf_sweep(buf)
                yield to_our_bins(psd, freqs)[0]
                buf = []
            stamp = s
            buf.append(line)

    def windows(self, quiet_rows: int = 48) -> Iterator[np.ndarray]:
        """Calibrated 12-row windows: the first `quiet_rows` sweeps (taken before any test transmission) set
        the gain and the band shape (ai/calibration.py), then every window is corrected the same way."""
        from .calibration import Calibrator
        cal, rows, start = Calibrator(), [], []
        for row in self.rows():
            if len(start) < quiet_rows:
                start.append(row)
                if len(start) == quiet_rows:
                    cal.calibrate_quiet([np.array(start[i:i + ROWS]) for i in range(0, quiet_rows, ROWS)])
                continue
            rows.append(row)
            if len(rows) == ROWS:
                yield cal.calibrate(np.array(rows))
                rows = rows[ROWS // 2:]

    def close(self) -> None:
        self.proc.terminate()


# ------------------------------------------------------------------ classify and evaluate
def classify(cnn, window_db: np.ndarray, with_cam: bool = True) -> dict:
    from .ood import UNKNOWN_RATIO, ratio
    r = cnn.predict(window_db, with_cam=with_cam)
    nov = ratio(r["emb"], r["k"])
    verdict = "unknown" if nov > UNKNOWN_RATIO and r["class"] != "clean" else r["class"]
    out = {"verdict": verdict, "class": r["class"], "confidence": float(r["confidence"]), "novelty": float(nov),
           "probs": r["probs"]}
    if with_cam:
        out["cam"] = r["cam"]
    return out


def evaluate(path: Path | str) -> dict:
    from .cnn import CNN
    rec, cnn = Recording(path), CNN()
    table: dict[str, Counter] = {}
    for w, lab, _ in rec.windows():
        v = classify(cnn, w, with_cam=False)["verdict"]
        table.setdefault(lab or "(no label)", Counter())[v] += 1
    correct = sum(c[lab] for lab, c in table.items() if lab != "(no label)")
    labelled = sum(sum(c.values()) for lab, c in table.items() if lab != "(no label)")
    return {"recording": rec.info(), "verdicts_by_label": {k: dict(v) for k, v in table.items()},
            "accuracy_pct": round(100 * correct / labelled, 1) if labelled else None}


def capture_from_sim(jammer: Optional[str], seconds: float, seed: int = 11) -> tuple[np.ndarray, list[str]]:
    """Record what the command post's receiver sees in the simulator: the same format as a real capture,
    used to test the replay path (and marked simulated, never presented as real data)."""
    from sim.benchmark import TRUE_CLASS
    from sim.experiment import DEFAULT_JAMMER_POS
    from sim.jammers import make_jammer
    from sim.phy import NumpyPhy
    from sim.world import SimWorld
    w = SimWorld("main", seed, "fixed_hopping", NumpyPhy())
    w.run_slots(20)
    if jammer:
        w.launch(make_jammer("J1", jammer, DEFAULT_JAMMER_POS[jammer], None, None))
    # the right answer: the class the CNN knows, or "unknown" for the never-seen comb
    label = ("unknown" if jammer == "novel" else TRUE_CLASS.get(jammer) or jammer) if jammer else "clean"
    rows, labels = [], []
    for _ in range(int(seconds / 0.01 / 2)):
        w.run_slots(2)
        win = w.sense("CP")
        rows.append(win)
        labels += [label] * len(win)
    return np.concatenate(rows), labels


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m ai.sources")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("convert-sweep", "convert-wide"):
        c = sub.add_parser(name)
        c.add_argument("src")
        c.add_argument("--out", required=True)
        c.add_argument("--label")
        c.add_argument("--source", default="")
        c.add_argument("--licence", default="")
    c = sub.add_parser("capture")
    c.add_argument("--jammer")
    c.add_argument("--seconds", type=float, default=4.0)
    c.add_argument("--out", required=True)
    c = sub.add_parser("eval")
    c.add_argument("files", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd in ("convert-sweep", "convert-wide"):
        if a.cmd == "convert-sweep":
            with open(a.src, encoding="utf-8") as fh:
                psd, freqs, t = parse_hackrf_sweep(fh)
        else:
            psd, freqs, t = parse_wide_csv(a.src)
        labels = [a.label] * len(psd) if a.label else None
        p = save_recording(a.out, psd, freqs, t, labels, source=a.source or Path(a.src).name, licence=a.licence)
        print(f"wrote {p} ({len(psd)} rows, {len(freqs)} bins, {freqs.min():.1f}-{freqs.max():.1f} MHz)")
    elif a.cmd == "capture":
        rows, labels = capture_from_sim(a.jammer, a.seconds)
        p = save_recording(a.out, rows, BIN_CENTRES, None, labels, source="ABHEDYA simulator capture", simulated=True,
                           note="Simulated, for testing the replay path. Not a real measurement.")
        print(f"wrote {p} ({len(rows)} rows)")
    else:
        for f in a.files:
            print(json.dumps(evaluate(f), indent=2))


if __name__ == "__main__":
    main()
