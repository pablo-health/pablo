# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The end-to-end harness's fake clearinghouse: what its 835s actually say.

``scripts/fake_clearinghouse.py`` lives outside the backend package and is
never imported by the app, but its output is read by the app's own 835
parser once the harness is running — so this proves the fake's ``PART-``,
``DENY-`` and ``NOSUM-`` remittances parse the way a real one would, using
the same code that will parse a real payer's, rather than eyeballing the
JSON.

The ``NOSUM-`` cases carry the most weight here. A fake that was *meant* to
contradict itself and did so in some second, unintended way would make the
browser test green for the wrong reason — and the sibling fake used by the
unit tests already has an accidental version of this bug, so the confusion
is not hypothetical.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from app.claims.remittance_lines import DENIED, disagreement_in, patient_responsibility_agrees
from app.claims.responses import parse_835

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fake_clearinghouse as fake

_CHARGE = "150"


def _claim(control_number: str, charge: str = _CHARGE) -> dict[str, Any]:
    return {
        "claimInformation": {
            "patientControlNumber": control_number,
            "claimChargeAmount": charge,
            "serviceLines": [
                {
                    "professionalService": {
                        "lineItemChargeAmount": charge,
                        "procedureCode": "90837",
                    }
                }
            ],
        }
    }


def _remittance_claim(control_number: str) -> Any:
    fake.state.claims[control_number] = _claim(control_number)
    report = fake._build_835_report(control_number)
    remittance = parse_835(report)[0]
    return remittance.claims[0]


def test_a_part_claim_pays_part_and_assigns_the_rest_to_the_patient() -> None:
    claim = _remittance_claim("PART-1")
    assert 0 < claim.paid_cents < claim.total_charge_cents
    assert claim.patient_responsibility_cents == claim.total_charge_cents - claim.paid_cents
    assert patient_responsibility_agrees(claim)


def test_a_deny_claim_denies_and_leaves_the_client_owing_the_charge() -> None:
    claim = _remittance_claim("DENY-1")
    assert claim.claim_status_code == DENIED
    assert claim.paid_cents == 0
    assert claim.patient_responsibility_cents == claim.total_charge_cents
    assert patient_responsibility_agrees(claim)


def test_anything_else_still_pays_in_full() -> None:
    claim = _remittance_claim("88659891")
    assert claim.paid_cents == claim.total_charge_cents
    assert claim.patient_responsibility_cents == 0
    assert patient_responsibility_agrees(claim)


def test_a_nosum_claim_states_a_client_share_its_lines_do_not_itemise() -> None:
    """The disagreement the browser test drives."""
    claim = _remittance_claim("NOSUM-1")

    assert claim.patient_responsibility_cents > 0
    assert not patient_responsibility_agrees(claim)


def test_the_nosum_disagreement_is_the_one_we_meant() -> None:
    """A fake that contradicted itself some OTHER way would make the browser
    test pass for a reason nobody chose."""
    claim = _remittance_claim("NOSUM-1")

    found = disagreement_in(claim)

    assert found is not None
    assert found.reason == "patient_responsibility"
    assert found.stated_cents == claim.patient_responsibility_cents
    assert found.computed_cents == 0


def test_a_nosum_claim_still_balances_everywhere_else() -> None:
    """Only the cross-check fires: the line and the claim both account for
    themselves, so the hold names one reason and nothing is ambiguous."""
    claim = _remittance_claim("NOSUM-1")
    [line] = claim.lines

    assert line.paid_cents + sum(a.amount_cents for a in line.adjustments) == line.charge_cents
    total_adjustments = sum(a.amount_cents for a in claim.adjustments) + sum(
        a.amount_cents for line in claim.lines for a in line.adjustments
    )
    assert claim.paid_cents + total_adjustments == claim.total_charge_cents


def test_a_nosum_claim_itemises_the_money_as_a_write_off() -> None:
    """Which is what makes it a disagreement rather than an arithmetic slip:
    the payer says the client owes it and the line says nobody does."""
    claim = _remittance_claim("NOSUM-1")
    [line] = claim.lines

    assert [a.group_code for a in line.adjustments] == ["CO"]


def test_a_forced_outcome_overrides_the_prefix() -> None:
    """How a browser test reaches these rules at all.

    A claim filed through the app gets a server-generated control number and
    cannot be given a prefix, so ``/_fake/deliver`` names the outcome
    instead. Both routes funnel through one decision.
    """
    fake.state.outcomes["88659891"] = "disagreeing"
    try:
        claim = _remittance_claim("88659891")

        assert not patient_responsibility_agrees(claim)
    finally:
        fake.state.outcomes.clear()


def test_a_claim_with_no_forced_outcome_and_no_prefix_is_still_paid_in_full() -> None:
    """Guards the override from leaking into every other claim."""
    fake.state.outcomes.clear()
    claim = _remittance_claim("88659892")

    assert claim.paid_cents == claim.total_charge_cents
    assert patient_responsibility_agrees(claim)
