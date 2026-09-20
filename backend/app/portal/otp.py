# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""One-time passcodes — generation, peppered hashing, constant-time verify.

The plaintext code is delivered once and never stored: only its HMAC-SHA256,
peppered with the portal signing key, is persisted on the challenge. So the
challenge table on its own yields no codes, and a leaked magic link still
cannot redeem.

Verification is constant-time, because a comparison that returns early leaks
how much of a guess was right.

A texted code is the step-up factor rather than a date of birth: a date of
birth is low-entropy, frequently known to whoever holds the forwarded link,
and unchangeable once guessed.
"""

from __future__ import annotations

import hmac
import secrets
from hashlib import sha256

_MIN_OTP_LENGTH = 4


def generate_otp(length: int = 6) -> str:
    """A cryptographically-random numeric code of ``length`` digits
    (leading zeros preserved)."""
    if length < _MIN_OTP_LENGTH:
        raise ValueError("one-time code length must be at least 4 digits")
    upper = 10**length
    return f"{secrets.randbelow(upper):0{length}d}"


def hash_otp(otp: str, *, pepper: str) -> str:
    """HMAC-SHA256 of the code under the pepper. Hex digest."""
    return hmac.new(pepper.encode(), otp.encode(), sha256).hexdigest()


def verify_otp(otp: str, *, otp_hash: str, pepper: str) -> bool:
    """Constant-time check of a candidate code against the stored hash."""
    return hmac.compare_digest(hash_otp(otp, pepper=pepper), otp_hash)
