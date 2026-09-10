# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The end-to-end harness's fake clearinghouse: what its 835s actually say.

``scripts/fake_clearinghouse.py`` lives outside the backend package and is
never imported by the app, but its output is read by the app's own 835
parser once the harness is running — so this proves the fake's ``PART-`` and
``DENY-`` remittances parse the way a real one would, using the same code
that will parse a real payer's, rather than eyeballing the JSON.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from app.claims.remittance_lines import DENIED, patient_responsibility_agrees
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
