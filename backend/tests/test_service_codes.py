# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The superbill's plain-English code names (``app.claims.service_codes``).

The browser offers the same list as suggestions when a clinician types a
code. Two copies of one list drift, and the drift is invisible: the
appointment form would call 90837 one thing and the superbill another. So the
browser's file is read here and the two are compared entry for entry.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.claims.service_codes import COMMON_SERVICE_CODES, describe_service_code

_BROWSER_LIST = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "serviceCodes.ts"
_ENTRY = re.compile(r'\{\s*code:\s*"([^"]+)",\s*description:\s*"([^"]+)"\s*\}')


def test_the_superbill_and_the_appointment_form_name_every_code_alike() -> None:
    browser = dict(_ENTRY.findall(_BROWSER_LIST.read_text()))
    assert browser, "no entries parsed from serviceCodes.ts"
    assert browser == COMMON_SERVICE_CODES


def test_a_known_code_is_described_however_it_was_typed() -> None:
    assert describe_service_code(" 90837 ") == "Therapy session — 60 minutes or more"


def test_an_unknown_code_has_no_description_rather_than_a_guess() -> None:
    assert describe_service_code("97110") is None
    assert describe_service_code(None) is None
