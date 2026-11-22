"""The radio interface the AI and the link layer talk to, so a real SDR can replace the simulator.

Everything above this line (AES-GCM frames, keyed hopping, the CNN, the defender, the safety guard) is
the same code whether the backend is simulated or real. A backend must provide:

  tune(channel)                 move both TX and RX to channel 0..7 (2410 + 10*channel MHz)
  set_tx_power(dbm)             transmit power (within the radio's limits)
  set_modulation(name)          'QPSK', 'BPSK' or 'BPSK_R2'
  send(frame)                   transmit one frame (bytes) in the current slot
  receive(timeout_s)            (frame bytes, rssi_dbm) or None
  sweep()                       one row of power per frequency bin over 2405-2485 MHz (for the CNN)
  gain_db                       the receive gain currently applied (for calibration; fixed gain recommended)

Backends
  SimBackend      two endpoints over our link model, with a settable attenuation: used to check the bench
                  scripts and to produce the simulator's predictions that the measurements are compared with
  SdrBackend      a real SDR through GNU Radio / SoapySDR. NOT YET RUN ON HARDWARE: the class documents the
                  exact flowgraph and fails loudly until it is wired and verified on the bench
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from sim.radio import NOISE_INBAND_DBM, packet_delivery

CHANNEL_MHZ = [2410 + 10 * c for c in range(8)]


@dataclass
class SimBackend:
    """A cabled link in software: TX power - attenuation - noise, the same BER curves as the simulator."""
    attenuation_db: float = 80.0
    tx_dbm: float = 0.0
    modulation: str = "QPSK"
    channel: int = 0
    jammer_dbm: Optional[float] = None          # an injected noise source on one channel (via a combiner)
    jammer_channel: int = 3
    seed: int = 0
    gain_db: float = 0.0

    def __post_init__(self):
        self.rng = np.random.default_rng(self.seed)
        self.peer_channel = 0
        self._air: list[bytes] = []

    def tune(self, channel: int) -> None:
        self.channel = int(channel)

    def tune_peer(self, channel: int) -> None:
        self.peer_channel = int(channel)

    def set_tx_power(self, dbm: float) -> None:
        self.tx_dbm = float(dbm)

    def set_modulation(self, name: str) -> None:
        self.modulation = name

    def rssi_dbm(self) -> float:
        return self.tx_dbm - self.attenuation_db

    def sinr_db(self) -> float:
        noise_mw = 10 ** (NOISE_INBAND_DBM / 10)
        if self.jammer_dbm is not None and self.jammer_channel == self.channel:
            noise_mw += 10 ** (self.jammer_dbm / 10)
        return self.rssi_dbm() - 10 * np.log10(noise_mw)

    def send(self, frame: bytes) -> None:
        if self.channel != self.peer_channel:
            return                                  # the other end is listening elsewhere: lost
        p = float(packet_delivery(np.array([self.sinr_db()]), self.modulation)[0])
        if self.rng.random() < p:
            self._air.append(frame)

    def receive(self, timeout_s: float = 0.01):
        if not self._air:
            return None
        return self._air.pop(0), self.rssi_dbm() + float(self.rng.normal(0, 1.0))

    def sweep(self) -> np.ndarray:
        from backend.protocol import FFT_BINS
        row = np.full(FFT_BINS, NOISE_INBAND_DBM - 10 * np.log10(FFT_BINS / 8) + self.rng.normal(0, 1, FFT_BINS))
        return row


def predicted_per(sinr_db: float, modulation: str = "QPSK") -> float:
    """The simulator's packet error rate for one frame (PACKET_BITS) at this SINR."""
    return 1.0 - float(packet_delivery(np.array([sinr_db]), modulation)[0])


class SdrBackend:
    """Real SDR. To wire on the bench (GNU Radio 3.10, gr-osmosdr or gr-soapy; HackRF One or ADALM-Pluto):

      TX: frame bytes -> CRC/FEC (same code rate as sim.radio) -> constellation modulator (QPSK, RRC 0.35,
          2 Msym/s, 8 samples/symbol = 16 MS/s) -> preamble -> osmosdr/soapy sink at 2410 + 10*ch MHz
      RX: source -> AGC OFF (fixed gain, report it as gain_db) -> correlate access code -> Costas loop ->
          symbol sync -> constellation decoder -> FEC decode -> frame bytes; RSSI = mean |x|^2 of the burst,
          converted to dBm with the calibration from ai/calibration.py
      Sweep: hackrf_sweep or a 128-bin FFT at 80 MS/s (2405-2485 MHz) -> ai/sources.LiveHackRF
      Retune: set_center_freq() per hop; measure the settling time and put it inside the slot guard

    Not run on hardware yet. Every method raises until the flowgraph above has been built and verified.
    """

    def __init__(self, device: str = "hackrf=0"):
        self.device = device
        raise NotImplementedError("SdrBackend is a wiring guide until verified on the bench: see docs/HARDWARE_TEST_PLAN.md")
