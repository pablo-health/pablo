# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A claim that passes every scrub rule, for the claim tests to start from.

Mirrors the recorded ``837p_request_test_payer.json`` — the vendor's
documented example person and dummy NPI, a random dummy EIN — so the wire
test can compare the mapping's output against that fixture value for value.
Nothing here is PHI. Each test mutates one thing and asserts on the finding
it expects.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from app.models.claims import (
    BillingProviderSnapshot,
    BillingSnapshot,
    Claim,
    ClaimLine,
    PersonSnapshot,
    RenderingProviderSnapshot,
    SubscriberSnapshot,
)

CLAIM_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
PATIENT_ID = "11111111-1111-4111-8111-111111111111"
COVERAGE_ID = "22222222-2222-4222-8222-222222222222"
PAYER_ROW_ID = "33333333-3333-4333-8333-333333333333"
APPOINTMENT_ID = "44444444-4444-4444-8444-444444444444"
USER_ID = "55555555-5555-4555-8555-555555555555"

BUILT_AT = datetime(2026, 9, 2, 15, 0, tzinfo=UTC)
SERVICE_DATE = date(2026, 9, 1)
TODAY = date(2026, 9, 6)


def person(**overrides: Any) -> PersonSnapshot:
    fields: dict[str, Any] = {
        "first_name": "John",
        "last_name": "Anon",
        "date_of_birth": date(2000, 1, 1),
        "sex": "M",
        "address_line1": "2222 Random St",
        "city": "Atlanta",
        "state": "GA",
        "postal_code": "303010000",
    }
    fields.update(overrides)
    return PersonSnapshot(**fields)


def billing_snapshot(**overrides: Any) -> BillingSnapshot:
    billing = {
        "legal_name": "Pablo Test Practice",
        "tax_id_last4": "9714",
        "tax_id_type": "ein",
        "npi": "1999999984",
        "address_line1": "123 Some St",
        "city": "Atlanta",
        "state": "GA",
        "postal_code": "303010000",
        "phone": "5553334444",
    }
    rendering = {
        "user_id": USER_ID,
        "first_name": "Jane",
        "last_name": "Smith",
        "npi": "1999999984",
        "taxonomy_code": "101YM0800X",
    }
    billing.update({k: v for k, v in overrides.items() if k in billing})
    rendering.update({k: v for k, v in overrides.items() if k in rendering})
    return BillingSnapshot(
        billing_provider=BillingProviderSnapshot(**billing),
        rendering_provider=RenderingProviderSnapshot(**rendering),
    )


def subscriber_snapshot(**overrides: Any) -> SubscriberSnapshot:
    fields: dict[str, Any] = {
        "member_id": "123456789",
        "group_number": "3335555",
        "plan_name": None,
        "relationship": "self",
        "coverage_active": True,
        "payer_id": "STEDI",
        "payer_name": "Stedi Test Payer",
        "subscriber": person(),
        "patient": person(),
    }
    fields.update(overrides)
    return SubscriberSnapshot(**fields)


def line(**overrides: Any) -> ClaimLine:
    fields: dict[str, Any] = {
        "id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "claim_id": CLAIM_ID,
        "patient_id": PATIENT_ID,
        "appointment_id": APPOINTMENT_ID,
        "line_number": 1,
        "line_control_number": "886598911",
        "service_date": SERVICE_DATE,
        "cpt": "90837",
        "modifiers": ["95"],
        "units": 1,
        "charge_cents": 15000,
        "dx_pointers": [1],
        "telehealth": True,
        "created_at": BUILT_AT,
    }
    fields.update(overrides)
    return ClaimLine(**fields)


def claim(**overrides: Any) -> Claim:
    """A valid, telehealth, self-subscriber claim with one 90837 line."""
    fields: dict[str, Any] = {
        "id": CLAIM_ID,
        "control_number": "88659891",
        "patient_id": PATIENT_ID,
        "coverage_id": COVERAGE_ID,
        "payer_id": PAYER_ROW_ID,
        "state": "draft",
        "frequency_code": "1",
        "parent_claim_id": None,
        "total_charge_cents": 15000,
        "total_paid_cents": 0,
        "diagnosis_codes": ["F41.1"],
        "place_of_service": "10",
        "billing_snapshot": billing_snapshot(),
        "subscriber_snapshot": subscriber_snapshot(),
        "created_at": BUILT_AT,
        "updated_at": BUILT_AT,
        "lines": [line()],
    }
    fields.update(overrides)
    return Claim(**fields)
