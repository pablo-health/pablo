# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The deadline defaults a new payer is created with.

The floor is on the table; the one exception — Medicare's year-long timely
filing window — is decided in code, and this is where that rule is pinned.
"""

from __future__ import annotations

import pytest
from app.db.models import (
    DEFAULT_APPEAL_DAYS,
    DEFAULT_CORRECTED_CLAIM_DAYS,
    DEFAULT_TIMELY_FILING_DAYS,
)
from app.services.coverage_intake import new_payer
from app.services.payer_defaults import (
    MEDICARE_TIMELY_FILING_DAYS,
    default_timely_filing_days,
    is_medicare_payer_id,
)


class TestMedicareRule:
    @pytest.mark.parametrize("payer_id", ["MEDICARE", "medicare-part-b", " Medicare_OH "])
    def test_medicare_prefix_is_medicare_whatever_the_case(self, payer_id: str) -> None:
        assert is_medicare_payer_id(payer_id)
        assert default_timely_filing_days(payer_id) == MEDICARE_TIMELY_FILING_DAYS

    @pytest.mark.parametrize("payer_id", ["87726", "AETNA", "BCBS-MEDICARE-ADVANTAGE", ""])
    def test_anything_else_gets_the_floor(self, payer_id: str) -> None:
        assert not is_medicare_payer_id(payer_id)
        assert default_timely_filing_days(payer_id) == DEFAULT_TIMELY_FILING_DAYS


class TestNewPayerDefaults:
    def test_commercial_payer_gets_the_common_floor(self) -> None:
        payer = new_payer(name="Aetna", payer_id="60054")

        assert payer.timely_filing_days == DEFAULT_TIMELY_FILING_DAYS == 90
        assert payer.corrected_claim_days == DEFAULT_CORRECTED_CLAIM_DAYS == 90
        assert payer.appeal_days == DEFAULT_APPEAL_DAYS == 180
        assert payer.enrollment_status == "none"
        assert payer.clearinghouse_payer_id is None

    def test_medicare_payer_gets_a_year_to_file(self) -> None:
        payer = new_payer(name="Medicare Part B", payer_id="MEDICARE-OH")

        assert payer.timely_filing_days == 365
        # Only the filing window is Medicare-specific.
        assert payer.corrected_claim_days == 90
        assert payer.appeal_days == 180

    def test_a_stated_deadline_wins_over_the_default(self) -> None:
        payer = new_payer(name="Medicare Part B", payer_id="MEDICARE-OH", timely_filing_days=120)

        assert payer.timely_filing_days == 120

    def test_name_and_id_are_trimmed(self) -> None:
        payer = new_payer(name="  Aetna ", payer_id=" 60054 ")

        assert payer.name == "Aetna"
        assert payer.payer_id == "60054"
