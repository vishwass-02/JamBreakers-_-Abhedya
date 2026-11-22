"""Hop sequences.

FIXED_PATTERN is the conventional FHSS baseline: a public, repeating pattern (anyone who watches for
8 slots knows it). keyed_channel() is ABHEDYA's sequence: both ends compute
HMAC-SHA256(key, slot) and map it onto the channels that are not blacklisted, so they always agree
and a jammer without the key cannot predict it.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Iterable

from .radio import N_CH

FIXED_PATTERN = [0, 3, 6, 1, 4, 7, 2, 5]
MIN_CHOICES = 3


def fixed_channel(slot: int) -> int:
    return FIXED_PATTERN[slot % len(FIXED_PATTERN)]


def keyed_index(key: bytes, slot: int) -> int:
    digest = hmac.new(key, b"hop" + slot.to_bytes(8, "big"), hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big")


def keyed_channel(key: bytes, slot: int, blacklist: Iterable[int] = (), avoid: Iterable[int] = (),
                  avoid_soft: Iterable[int] = ()) -> int:
    """next channel = HMAC-SHA256(key, slot) mod |candidates|.

    Candidates, in order of preference (the first set with at least MIN_CHOICES channels wins):
      1. not blacklisted, not in `avoid`, not in `avoid_soft`
      2. not blacklisted, not in `avoid`
      3. not in `avoid` (the blacklist gives way)
      4. not blacklisted / anything
    `avoid` = the last 3 channels (a follower re-tunes ~25 ms late, so it always sits on one of them);
    `avoid_soft` = right next to the last channel. Both ends know the slot, the key, the recent channels
    and the blacklist (sent in authenticated frames), so they always compute the same channel."""
    bl, hard, soft = set(blacklist), set(avoid), set(avoid_soft)
    every = list(range(N_CH))
    allowed = [c for c in every if c not in bl]
    tiers = ([c for c in allowed if c not in hard | soft], [c for c in allowed if c not in hard],
             [c for c in every if c not in hard], allowed, every)
    # Unpredictability needs real choices: with one candidate per slot the "keyed" sequence turns into
    # a short fixed loop that a pattern-learning follower locks onto. So the blacklist gives way first.
    for cand in tiers:
        if len(cand) >= MIN_CHOICES:
            return cand[keyed_index(key, slot) % len(cand)]
    for cand in tiers:
        if cand:
            return cand[keyed_index(key, slot) % len(cand)]
    return 0


def rendezvous_channel(key: bytes, epoch: int) -> int:
    """Key-derived channel where a lost node listens to rejoin; changes every key epoch."""
    digest = hmac.new(key, b"rdv" + epoch.to_bytes(8, "big"), hashlib.sha256).digest()
    return digest[0] % N_CH
