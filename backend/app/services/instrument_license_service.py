# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Instrument licences — what this practice has permission to use.

Some instruments may be reproduced but not used freely. The registry marks
those ``attestation_required`` (see
:mod:`app.outcome_measures.instruments`), and a practice records that it
holds whatever the instrument's rights require before the form builder
offers it.

**Recording permission is an act, not a setting.** Attesting writes a row
with who did it and when; withdrawing stamps the row rather than deleting
it; attesting again writes a new row and withdraws the one it replaces. So
the table is a history and the question everything else asks — is this
instrument licensed here, right now — has one answer, which the partial
unique index in the schema is what guarantees.

**The gate is at publish, and nowhere after.** A form that went live under
an attestation keeps working when the attestation is withdrawn. Withdrawing
says what may go on a NEW form; a published version is what somebody's
answers were answers to, and nothing may reach back into one.

Two things this deliberately does not do. It does not check a licence with
a publisher — there is nobody to ask, and the practice is the one who holds
it. And it does not touch an instrument the registry calls ``never_ship``:
there is no permission a practice can record here that would make this
engine ship a form it does not have.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from ..outcome_measures.instruments import INSTRUMENT_REGISTRY
from ..utcnow import utc_now

if TYPE_CHECKING:
    from ..repositories.instrument_license import InstrumentLicenseRepository


class UnlicensableInstrumentError(ValueError):
    """An instrument whose use is not a thing a practice can attest to.

    Either the code is not in the registry at all, or it is one the registry
    calls ``public_domain`` (nothing to attest) or ``never_ship`` (no
    attestation would change what this engine has).
    """


class InstrumentLicenseService:
    """The practice's attestations, with the one-active-row rule enforced."""

    def __init__(self, repo: InstrumentLicenseRepository) -> None:
        self._repo = repo

    def list_active(self) -> list[dict[str, object]]:
        """Every attestation in force."""
        return self._repo.list_active()

    def attested_codes(self) -> frozenset[str]:
        """The codes this practice may put on a form, as a set.

        What the publisher consults. A set rather than the rows, because
        the only thing the gate asks is membership.
        """
        return frozenset(str(row["instrument_code"]) for row in self._repo.list_active())

    def active_for_code(self, instrument_code: str) -> dict[str, object] | None:
        return self._repo.active_for_code(instrument_code)

    def attest(
        self,
        instrument_code: str,
        attested_by: str,
        *,
        license_reference: str | None = None,
        notes: str | None = None,
    ) -> dict[str, object]:
        """Record that this practice holds permission to use *instrument_code*.

        Attesting over an attestation that is already in force withdraws the
        old row first and writes a new one. It reads as re-recording the
        same permission and it is: who attested and when is the fact worth
        keeping, and correcting a licence reference should not silently
        rewrite whose name is against it.

        Raises :class:`UnlicensableInstrumentError` for a code the registry
        does not mark ``attestation_required``.
        """
        self._require_restricted(instrument_code)
        now = utc_now()

        held = self._repo.active_for_code(instrument_code)
        if held is not None:
            self._repo.revoke(str(held["id"]), now)

        return self._repo.add(
            {
                "id": str(uuid.uuid4()),
                "instrument_code": instrument_code,
                "attested_by": attested_by,
                "attested_at": now,
                "license_reference": _blank_to_none(license_reference),
                "notes": _blank_to_none(notes),
                "revoked_at": None,
            }
        )

    def revoke(self, instrument_code: str) -> dict[str, object] | None:
        """Withdraw the attestation in force, or ``None`` if there is none.

        Forms already published that ask this instrument are untouched. See
        the module docstring: the gate is at publish.
        """
        held = self._repo.active_for_code(instrument_code)
        if held is None:
            return None
        return self._repo.revoke(str(held["id"]), utc_now())

    def _require_restricted(self, instrument_code: str) -> None:
        defn = INSTRUMENT_REGISTRY.get(instrument_code)
        if defn is None or defn.rights != "attestation_required":
            raise UnlicensableInstrumentError(instrument_code)


def _blank_to_none(value: str | None) -> str | None:
    """A field the settings screen sent empty, stored as unset."""
    return value if value is not None and value.strip() else None


__all__ = ["InstrumentLicenseService", "UnlicensableInstrumentError"]
