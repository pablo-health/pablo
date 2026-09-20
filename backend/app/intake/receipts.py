# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The short code a patient is given when they hand a form in.

A receipt is not a credential. It unlocks nothing, it is not a second
factor, and knowing one gets nobody near a record — which is why it can be
eight characters rather than a token, and why it is safe to print, read out
or quote in an email. What it is for is the conversation that happens when
somebody is not sure the form arrived: they read the code out, and the
practice can find the row it names.

Two properties make it usable in that conversation.

**The alphabet is unambiguous.** No ``0`` or ``O``, no ``1`` or ``I`` or
``L``, and no ``U`` — the characters a person mishears, mistypes or
mistranscribes when reading a code aloud down a phone line. What is left is
thirty characters, which is the point: a shorter alphabet somebody can
actually dictate beats a longer one they cannot.

**It is random, not sequential.** A counter would leak how many forms a
practice has taken, and two practices' codes would look alike enough to be
quoted at the wrong one. Thirty to the eighth is about 6.6e11 codes, so a
practice with a million submissions still has a collision probability
small enough that the unique index is a backstop rather than a loop the
caller expects to go round.

Uniqueness itself is the database's job, per practice: the column carries a
unique index inside the tenant schema, and :func:`new_receipt_code` is
called again when it refuses. That is the only correct place for it — two
requests can generate the same code at the same moment, and only the index
can arbitrate.
"""

from __future__ import annotations

import secrets

#: The characters a receipt is built from. Excludes ``0``/``O``, ``1``/``I``
#: /``L`` and ``U``, which are what a person gets wrong reading a code out.
RECEIPT_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"

#: How long a receipt is. Long enough that a practice never sees two, short
#: enough to read down a phone line in one breath.
RECEIPT_LENGTH = 8


def new_receipt_code() -> str:
    """One random receipt code.

    Uses :mod:`secrets` rather than :mod:`random` — not because the code is
    a credential, but because a predictable sequence would let anyone who
    saw one receipt guess the next, and guessing receipts is how somebody
    would go looking for forms that are not theirs.
    """
    return "".join(secrets.choice(RECEIPT_ALPHABET) for _ in range(RECEIPT_LENGTH))


__all__ = ["RECEIPT_ALPHABET", "RECEIPT_LENGTH", "new_receipt_code"]
