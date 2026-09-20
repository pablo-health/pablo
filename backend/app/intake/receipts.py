# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a patient is given when they hand a form in: a code, and any notes.

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

A receipt can also carry a note, and there is one thing it says today. A
form that branches can be answered in an order that leaves an answer behind
somebody's own later change: they said yes, answered the question that
opened, went back and said no. The question is no longer asked, so what
they typed into it is not handed in — and being told that on the way out is
better than finding it missing from the chart later. See
:func:`withheld_answers_note`.
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


def withheld_answers_note(count: int) -> str | None:
    """The note for *count* answers to questions the form stopped asking.

    ``None`` when there were none, which is the ordinary case and the one a
    receipt says nothing about — a screen that explains what did not happen
    every time somebody hands a form in is a screen teaching branching to
    people who did not ask.

    One sentence when there were. It says what happened rather than why the
    form works this way: the patient changed an earlier answer, the
    questions that answer had opened are no longer asked, and what they had
    already put for them is not part of what the practice receives. Finding
    that out here is better than the clinician finding an answer in the
    chart to a question this patient was not, in the end, asked.
    """
    if count < 1:
        return None
    if count == 1:
        return "One question stopped applying as you answered, so your answer to it wasn't sent."
    return (
        f"{count} questions stopped applying as you answered, so your answers to them weren't sent."
    )


__all__ = [
    "RECEIPT_ALPHABET",
    "RECEIPT_LENGTH",
    "new_receipt_code",
    "withheld_answers_note",
]
