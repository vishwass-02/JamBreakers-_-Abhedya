"""Signal-level synthesis: build the IQ the receiver would see and turn it into a spectrogram window.

Every emitter (our QPSK transmitter, each jammer, a friendly emitter) is described by an
EmitterSpec. A backend turns the list into a 12 x 128 spectrogram (dBm per 625 kHz bin) covering a
0.3 ms window: 12 time rows, each the average of 4 Hann-windowed 128-point FFTs.

Two backends with the same interface:
  NumpyPhy      the reference implementation (always available)
  GnuRadioPhy   the same chain as a GNU Radio flowgraph (sim/phy_gnuradio.py), used when
                SIGNAL_BACKEND=gnuradio and GNU Radio imports; falls back to NumpyPhy otherwise.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional, Protocol

import numpy as np

from .radio import (
    FFTS_PER_ROW, FS, NFFT, NOISE_BAND_DBM, RRC_ALPHA, SYMBOL_RATE, WINDOW_ROWS, WINDOW_S, WINDOW_SAMPLES,
    dbm_to_mw,
)

log = logging.getLogger("aegis.phy")
ROW_SAMPLES = FFTS_PER_ROW * NFFT            # 512
ROW_DT = WINDOW_S / WINDOW_ROWS              # 25 us of real time per row


@dataclass
class EmitterSpec:
    """One signal arriving at the receiver during the window."""
    kind: str                                 # 'qpsk' | 'noise' | 'chirp' | 'ofdm'
    power_dbm: float                          # mean received power (before fading)
    center_hz: float = 0.0                    # baseband offset from the band centre
    bw_hz: float = 5e6                        # for 'noise' and 'ofdm'
    gate: Optional[np.ndarray] = None         # (12,) on/off per row; None = always on
    chirp_f0_hz: float = 0.0                  # 'chirp': frequency at window start (baseband)
    chirp_rate_hz_s: float = 0.0              # 'chirp': sweep rate
    k_db: Optional[float] = None              # Rician K for block fading; None = no fading
    label: str = ""                           # for debugging


class Phy(Protocol):
    name: str

    def synthesize(self, emitters: list[EmitterSpec], rng: np.random.Generator) -> np.ndarray:
        """Return a (12, 128) spectrogram in dBm per bin, lowest frequency first."""
        ...


# ---------- unit-power waveform generators (shared by both backends) ----------
@lru_cache(maxsize=4)
def rrc_taps(sps: int, span: int = 6, alpha: float = RRC_ALPHA) -> np.ndarray:
    t = (np.arange(-span * sps // 2, span * sps // 2 + 1)) / sps
    taps = np.zeros_like(t)
    for i, ti in enumerate(t):
        if abs(ti) < 1e-9:
            taps[i] = 1 - alpha + 4 * alpha / np.pi
        elif abs(abs(ti) - 1 / (4 * alpha)) < 1e-9:
            taps[i] = alpha / np.sqrt(2) * ((1 + 2 / np.pi) * np.sin(np.pi / (4 * alpha))
                                            + (1 - 2 / np.pi) * np.cos(np.pi / (4 * alpha)))
        else:
            taps[i] = (np.sin(np.pi * ti * (1 - alpha)) + 4 * alpha * ti * np.cos(np.pi * ti * (1 + alpha))) / \
                      (np.pi * ti * (1 - (4 * alpha * ti) ** 2))
    return taps / np.sqrt(np.sum(taps ** 2))


def qpsk_baseband(n: int, rng: np.random.Generator, symbol_rate: float = SYMBOL_RATE) -> np.ndarray:
    sps = int(round(FS / symbol_rate))
    taps = rrc_taps(sps)
    n_sym = n // sps + len(taps) // sps + 2
    sym = ((rng.integers(0, 2, n_sym) * 2 - 1) + 1j * (rng.integers(0, 2, n_sym) * 2 - 1)) / np.sqrt(2)
    up = np.zeros(n_sym * sps, dtype=complex)
    up[::sps] = sym
    y = np.convolve(up, taps)[len(taps) // 2: len(taps) // 2 + n]
    return y / np.sqrt(np.mean(np.abs(y) ** 2) + 1e-30)


@lru_cache(maxsize=64)
def _band_mask(n: int, bw_hz: float, edge: str) -> tuple[np.ndarray, float]:
    f = np.fft.fftfreq(n, 1 / FS)
    half = bw_hz / 2
    if edge == "soft":
        mask = np.clip((half - np.abs(f)) / (0.2 * half) + 0.5, 0, 1)
        mask = 0.5 - 0.5 * np.cos(np.pi * mask)
    else:  # 'ofdm': flat top, steep edges, null on the DC subcarrier
        mask = (np.abs(f) <= half).astype(float)
        mask[np.abs(f) < FS / n * 1.5] = 0.0
    # expected output power of unit complex noise through the mask, for normalisation
    return mask, float(np.sqrt(2 * np.sum(mask ** 2)) / n)


def band_noise(n: int, rng: np.random.Generator, bw_hz: float, edge: str = "soft") -> np.ndarray:
    """Complex noise filled into [-bw/2, bw/2] in the frequency domain (unit mean power)."""
    mask, norm = _band_mask(n, round(bw_hz, -3), edge)
    spec = rng.standard_normal(n) + 1j * rng.standard_normal(n)
    return np.fft.ifft(spec * mask) / norm


def chirp(n: int, t0: float, f0: float, rate: float) -> np.ndarray:
    """Linear sweep across the whole band, wrapping at the edges. Row r covers real time t0 + r*ROW_DT."""
    out = np.empty(n, dtype=complex)
    k = np.arange(ROW_SAMPLES) / FS
    for r in range(n // ROW_SAMPLES):
        t = t0 + r * ROW_DT + k
        f = (f0 + rate * (t - t0) + FS / 2) % FS - FS / 2
        phase = 2 * np.pi * np.cumsum(f) / FS
        out[r * ROW_SAMPLES:(r + 1) * ROW_SAMPLES] = np.exp(1j * phase)
    return out


@lru_cache(maxsize=256)
def _rotator(n: int, f_hz: float) -> np.ndarray:
    return np.exp(2j * np.pi * f_hz * np.arange(n) / FS)


def shift(x: np.ndarray, f_hz: float) -> np.ndarray:
    return x * _rotator(len(x), round(f_hz, -3))


def gate_samples(gate: Optional[np.ndarray], n: int) -> np.ndarray | float:
    if gate is None:
        return 1.0
    g = np.asarray(gate, dtype=float)
    return np.repeat(g, n // len(g))


def fading_gain(rng: np.random.Generator, k_db: Optional[float]) -> complex:
    if k_db is None:
        return 1.0
    k = 10 ** (k_db / 10)
    return np.sqrt(k / (k + 1)) * np.exp(1j * rng.uniform(0, 2 * np.pi)) + \
        np.sqrt(1 / (2 * (k + 1))) * (rng.standard_normal() + 1j * rng.standard_normal())


def emitter_waveform(e: EmitterSpec, rng: np.random.Generator, n: int = WINDOW_SAMPLES, t0: float = 0.0) -> np.ndarray:
    """Unit-power-before-gating waveform for one emitter, already shifted to its centre frequency."""
    if e.kind == "qpsk":
        x = shift(qpsk_baseband(n, rng), e.center_hz)
    elif e.kind == "noise":
        x = shift(band_noise(n, rng, e.bw_hz, "soft"), e.center_hz)
    elif e.kind == "ofdm":
        x = shift(band_noise(n, rng, e.bw_hz, "ofdm"), e.center_hz)
    elif e.kind == "chirp":
        x = chirp(n, t0, e.chirp_f0_hz, e.chirp_rate_hz_s)
    else:
        raise ValueError(f"unknown emitter kind {e.kind}")
    return x * gate_samples(e.gate, n)


def spectrogram_db(x: np.ndarray) -> np.ndarray:
    """(WINDOW_SAMPLES,) complex -> (12, 128) dBm per bin, fftshifted (lowest frequency first)."""
    w = np.hanning(NFFT)
    frames = x.reshape(WINDOW_ROWS, FFTS_PER_ROW, NFFT) * w
    X = np.fft.fftshift(np.fft.fft(frames, axis=-1), axes=-1)
    p = (np.abs(X) ** 2).mean(axis=1) / (np.sum(w ** 2) * NFFT)
    return 10 * np.log10(np.maximum(p, 1e-30))


class NumpyPhy:
    name = "numpy"

    def synthesize(self, emitters: list[EmitterSpec], rng: np.random.Generator, t0: float = 0.0) -> np.ndarray:
        n = WINDOW_SAMPLES
        x = np.sqrt(dbm_to_mw(NOISE_BAND_DBM) / 2) * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
        for e in emitters:
            amp = np.sqrt(dbm_to_mw(e.power_dbm)) * fading_gain(rng, e.k_db)
            x = x + amp * emitter_waveform(e, rng, n, t0)
        return spectrogram_db(x)


def get_phy(name: str) -> Phy:
    """'gnuradio' if requested and importable, else 'numpy'."""
    if name == "gnuradio":
        try:
            from .phy_gnuradio import GnuRadioPhy
            return GnuRadioPhy()
        except Exception as exc:  # ImportError, or a broken install
            log.warning("GNU Radio backend unavailable (%s); using NumPy fallback", exc)
    return NumpyPhy()
