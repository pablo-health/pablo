# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Contract tests against the exclusion lists themselves. Opt-in; they use the network.

``test_exclusions.py`` proves the parser against a captured file. This proves
the captured file still resembles the live one — which is the failure the
captured fixture cannot catch on its own, because a fixture agrees with
whoever captured it forever, including after the producer has moved on.

That failure has already been paid for once here: a pipeline with 86 passing
tests built on authored fixtures, reporting a parse it had never once done
against the real shape. The lesson taken from it was to capture the fixture.
The other half of the lesson is this file: something has to notice when the
capture goes stale.

RUN THEM WITH::

    PABLO_LIVE_EXCLUSIONS=1 pytest backend/tests/test_exclusions_live.py

Off by default, and deliberately not in the unit suite's path. A test that
fails because somebody else's web server is having a bad afternoon teaches the
team to ignore red, which costs more than the drift it catches. The intended
home is a scheduled run, where a failure means "go and look" rather than
"your change is broken".

CHEAP ON PURPOSE. The live LEIE is 15 MB. The header is the part that encodes
the contract, so these stream the response and stop after the first line rather
than pulling the file to assert one thing about it.

WHEN ONE FAILS, the file has moved and the parser has not. Refresh the captured
fixture, re-scrub it, and fix ``LEIE_COLUMNS`` — in that order, because the
fixture is the evidence the new layout was real.
"""

from __future__ import annotations

import csv
import os
from datetime import UTC, datetime

import httpx
import pytest
from app.credentialing.exclusions import (
    LEIE_COLUMNS,
    LEIE_DOWNLOAD_URL,
    SAM_BASE_URL,
    Outcome,
    Subject,
    check_sam,
)
from app.settings import get_settings

live = pytest.mark.skipif(
    os.environ.get("PABLO_LIVE_EXCLUSIONS") != "1",
    reason="Network test. Set PABLO_LIVE_EXCLUSIONS=1 to run it.",
)


def _first_line(url: str) -> str:
    """The first line of a large remote file, without fetching the rest."""
    with httpx.stream("GET", url, timeout=30.0, follow_redirects=True) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            return line
    return ""


@live
class TestLeieIsStillTheFileWeParse:
    def test_the_published_columns_are_the_ones_we_read(self) -> None:
        """The whole contract, in one assertion.

        Every field this module resolves is looked up by name from this header.
        A column renamed, dropped or reordered upstream turns matching into
        nonsense quietly — a clinician reported clear because the NPI column
        moved is the exact shape of failure worth a scheduled job.
        """
        header = next(csv.reader([_first_line(LEIE_DOWNLOAD_URL)]))
        published = tuple(column.strip().upper() for column in header)

        assert published == LEIE_COLUMNS, (
            "OIG's LEIE layout has changed. Refresh the captured fixture "
            "(backend/tests/fixtures/leie_sample.csv), re-scrub it, then update "
            f"LEIE_COLUMNS. Published: {published}"
        )

    def test_the_file_is_served_at_all(self) -> None:
        """Separated from the layout check so the two failures read differently.

        'OIG moved the file' and 'OIG changed the columns' want different
        people doing different things.
        """
        response = httpx.head(LEIE_DOWNLOAD_URL, timeout=30.0, follow_redirects=True)
        assert response.status_code == httpx.codes.OK


@live
class TestSamAnswersTheWayWeAsk:
    def test_a_configured_key_gets_a_real_answer(self) -> None:
        """Skipped rather than failed where no key is configured.

        No key is a deployment fact, not a defect — the same distinction the
        module itself draws. What this asserts is the one thing worth
        asserting: with a key, SAM gives an answer that is not UNAVAILABLE.
        """
        api_key = getattr(get_settings(), "sam_gov_api_key", None)
        if not api_key:
            pytest.skip("No SAM.gov API key configured for this deployment.")

        check = check_sam(
            # A surname chosen to be common enough that the endpoint is
            # exercised, and nobody in particular is being asked about.
            Subject(last_name="Smith"),
            checked_at=datetime.now(UTC),
            api_key=api_key,
            base_url=SAM_BASE_URL,
        )
        assert check.outcome is not Outcome.UNAVAILABLE, check.unavailable_reason
