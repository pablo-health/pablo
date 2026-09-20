# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The code a patient is given when they hand an intake form in.

Small enough to state completely: the right length, drawn from an alphabet
somebody can read down a phone line, and different every time. The last one
matters most — a receipt that repeated would put two people's submissions
under one name, and the unique index would turn that into a failed submit
rather than a wrong answer, but only after somebody hit it.
"""

from __future__ import annotations

from app.intake.receipts import RECEIPT_ALPHABET, RECEIPT_LENGTH, new_receipt_code


class TestTheAlphabet:
    def test_it_leaves_out_the_characters_people_mishear(self) -> None:
        for confusable in "01ILOU":
            assert confusable not in RECEIPT_ALPHABET, confusable

    def test_no_character_appears_twice(self) -> None:
        assert len(set(RECEIPT_ALPHABET)) == len(RECEIPT_ALPHABET)

    def test_it_is_upper_case_only(self) -> None:
        """A code is read out and typed back; case would be a second thing to get right."""
        assert RECEIPT_ALPHABET.upper() == RECEIPT_ALPHABET


class TestTheCode:
    def test_it_is_the_declared_length(self) -> None:
        assert len(new_receipt_code()) == RECEIPT_LENGTH

    def test_every_character_comes_from_the_alphabet(self) -> None:
        drawn = "".join(new_receipt_code() for _ in range(200))
        assert set(drawn) <= set(RECEIPT_ALPHABET)

    def test_two_hundred_draws_are_two_hundred_codes(self) -> None:
        """Not a proof of uniqueness — the index is that — but a smoke alarm.

        A generator that had lost its randomness (a seeded RNG, a cached
        value, a constant) would fail here rather than in production on the
        second submission of the day.
        """
        assert len({new_receipt_code() for _ in range(200)}) == 200
