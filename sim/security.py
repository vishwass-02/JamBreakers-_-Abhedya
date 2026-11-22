"""Secure link layer: mission keys, HKDF epochs, AES-256-GCM frames, replay window, keyed hopping keys.

Keys
  mission key   32 bytes, pre-loaded on every radio before deployment and rotated per mission.
                (In the simulation it is derived from the seed and mission number, so twin worlds and
                benchmark replays share it. On real radios it comes from the key loader.)
  epoch keys    HKDF-SHA256(mission key, info = purpose || epoch), a new epoch every 60 s:
                  'enc'  AES-256-GCM key for frames
                  'hop'  HMAC key for the hop sequence
                  'rdv'  HMAC key for the rendezvous channel
                Compromising one epoch's keys reveals nothing about other epochs.

Frame (58 bytes = 464 bits, the packet size the link budget uses)
  header (10 B, authenticated, not encrypted): version | type | src | dst | epoch (2) | seq (4)
  ciphertext (32 B telemetry, or a control message) + GCM tag (16 B)
  nonce (12 B) = src | epoch | seq | 00000  -> unique per key, because seq never repeats per sender per epoch

Receiver checks, in order: version, known sender, epoch (current or previous, for clock skew),
replay window (64 frames per sender), then the GCM tag. Anything else is rejected with a reason.
"""
from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass, field
from typing import Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

VERSION = 1
EPOCH_S = 60.0
REPLAY_WINDOW = 64
HEADER = struct.Struct(">BBBBHI")            # 10 bytes
HEADER_LEN = HEADER.size
TELEMETRY_LEN = 32
TAG_LEN = 16
T_DATA, T_CONTROL = 1, 2
BROADCAST = 0xFF


def mission_key(seed: int, mission: int) -> bytes:
    """Simulation stand-in for the key loader: deterministic per (seed, mission)."""
    # "AEGIS-LINK" labels below are fixed protocol constants (domain separation), kept from before the ABHEDYA
    # rename: changing them would change every key, hop sequence and published result.
    return hashlib.sha256(b"AEGIS-LINK mission key|" + str(seed).encode() + b"|" + str(mission).encode()).digest()


def fingerprint(key: bytes) -> str:
    """Public fingerprint of a key (safe to display; reveals nothing about the key)."""
    h = hashlib.sha256(b"AEGIS-LINK fingerprint|" + key).hexdigest()[:16].upper()
    return ":".join(h[i:i + 4] for i in range(0, 16, 4))


def hkdf(key: bytes, info: bytes, length: int = 32) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info).derive(key)


class KeySchedule:
    def __init__(self, mission_key_bytes: bytes):
        self.mk = mission_key_bytes
        self._cache: dict[tuple[str, int], bytes] = {}
        self._aead: dict[int, AESGCM] = {}

    def epoch_of(self, t: float) -> int:
        return int(t // EPOCH_S)

    def key(self, purpose: str, epoch: int) -> bytes:
        k = (purpose, epoch)
        if k not in self._cache:
            if len(self._cache) > 64:
                self._cache.clear()
            self._cache[k] = hkdf(self.mk, b"AEGIS-LINK|" + purpose.encode() + b"|" + epoch.to_bytes(4, "big"))
        return self._cache[k]

    def aead(self, epoch: int) -> AESGCM:
        a = self._aead.get(epoch)
        if a is None:
            if len(self._aead) > 8:
                self._aead.clear()
            a = self._aead[epoch] = AESGCM(self.key("enc", epoch))
        return a


def nonce(src: int, epoch: int, seq: int) -> bytes:
    return bytes([src]) + epoch.to_bytes(2, "big") + seq.to_bytes(4, "big") + b"\x00" * 5


def seal(ks: KeySchedule, ftype: int, src: int, dst: int, epoch: int, seq: int, payload: bytes) -> bytes:
    header = HEADER.pack(VERSION, ftype, src, dst, epoch & 0xFFFF, seq)
    return header + ks.aead(epoch).encrypt(nonce(src, epoch, seq), payload, header)


@dataclass
class OpenResult:
    ok: bool
    reason: str
    src: Optional[int] = None
    seq: Optional[int] = None
    ftype: Optional[int] = None
    payload: Optional[bytes] = None


@dataclass
class ReplayWindow:
    """RFC 4303-style sliding window: highest seq seen + bitmap of the 64 before it."""
    top: int = -1
    bits: int = 0

    def check(self, seq: int) -> Optional[str]:
        if self.top < 0 or seq > self.top:
            return None
        if self.top - seq >= REPLAY_WINDOW:
            return f"sequence {seq} is older than the {REPLAY_WINDOW}-frame replay window"
        if (self.bits >> (self.top - seq)) & 1:
            return f"sequence {seq} was already received (replay)"
        return None

    def accept(self, seq: int) -> None:
        if seq > self.top:
            shift = seq - self.top if self.top >= 0 else 0
            self.bits = ((self.bits << shift) | 1) & ((1 << REPLAY_WINDOW) - 1)
            self.top = seq
        else:
            self.bits |= 1 << (self.top - seq)


@dataclass
class Receiver:
    ks: KeySchedule
    known: set[int]
    windows: dict[tuple[int, int], ReplayWindow] = field(default_factory=dict)

    def open(self, frame: bytes, now_epoch: int) -> OpenResult:
        if len(frame) < HEADER_LEN + TAG_LEN:
            return OpenResult(False, "frame too short")
        ver, ftype, src, dst, ep16, seq = HEADER.unpack(frame[:HEADER_LEN])
        if ver != VERSION:
            return OpenResult(False, f"unknown version {ver}")
        if src not in self.known:
            return OpenResult(False, f"sender {src} is not in this mission")
        epoch = now_epoch if (now_epoch & 0xFFFF) == ep16 else now_epoch - 1
        if (epoch & 0xFFFF) != ep16:
            return OpenResult(False, f"key epoch {ep16} is not current (expected {now_epoch & 0xFFFF})")
        win = self.windows.setdefault((src, epoch), ReplayWindow())
        why = win.check(seq)
        if why:
            return OpenResult(False, why, src, seq, ftype)
        try:
            pt = self.ks.aead(epoch).decrypt(nonce(src, epoch, seq), frame[HEADER_LEN:], frame[:HEADER_LEN])
        except InvalidTag:
            return OpenResult(False, "GCM authentication tag does not verify (frame altered or wrong key)", src, seq, ftype)
        win.accept(seq)
        if len(self.windows) > 256:   # drop windows of old epochs
            for k in [k for k in self.windows if k[1] < now_epoch - 1]:
                self.windows.pop(k, None)
        return OpenResult(True, "verified", src, seq, ftype, pt)


def telemetry(node_index: int, t: float, pos, status: int) -> bytes:
    """32-byte telemetry payload: node, time, position, status, padding."""
    return struct.pack(">BdfffB", node_index, t, float(pos[0]), float(pos[1]), float(pos[2]), status).ljust(TELEMETRY_LEN, b"\x00")


def parse_telemetry(pt: bytes) -> dict:
    n, t, x, y, z, st = struct.unpack(">BdfffB", pt[:22])
    return {"node": n, "t": round(t, 3), "pos": [round(x, 1), round(y, 1), round(z, 1)], "status": st}


def random_key() -> bytes:
    return os.urandom(32)
