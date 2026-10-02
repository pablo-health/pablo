# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading a website's ``theme.json``: what the portal takes from it, what it
skips and why.

Storing the theme with a version, rolling back and the portal host's answer
are proven against Postgres in
``tests_integration/database/test_practice_sites_db.py``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.sites.theme import (
    CONTRAST_PAIRS,
    MIN_CONTRAST,
    PORTAL_COLORS,
    PracticeTheme,
    ThemeReport,
    contrast_ratio,
    normalize_color,
    read_theme,
    storable_theme,
    stored_theme,
)

FULL = {
    "version": 1,
    "colors": {
        "accent": "#24504c",
        "accentText": "#ffffff",
        "background": "#fbf8f3",
        "surface": "#ffffff",
        "text": "#1d2726",
        "mutedText": "#55605e",
    },
    "fonts": {"heading": "Fraunces", "body": "Inter"},
    "radius": "md",
}


def _read(value: Any) -> ThemeReport:
    report = read_theme(json.dumps(value).encode())
    assert report is not None
    return report


def _skipped(report: ThemeReport) -> dict[str, str]:
    return {s.field: s.reason for s in report.skipped}


def test_a_website_without_theme_json_has_no_theme() -> None:
    assert read_theme(None) is None


def test_a_full_theme_applies_whole() -> None:
    report = _read(FULL)

    assert report.skipped == []
    assert report.theme is not None
    assert storable_theme(report.theme) == FULL


def test_the_stored_form_reads_back_as_the_same_theme() -> None:
    theme = _read(FULL).theme

    assert stored_theme(storable_theme(theme)) == theme
    assert stored_theme(None) is None


def test_the_report_round_trips_through_its_json_form() -> None:
    report = _read({**FULL, "radius": "huge"})

    assert ThemeReport.model_validate(report.model_dump(mode="json")) == report


@pytest.mark.parametrize(
    ("value", "expected"),
    [("#ABC", "#aabbcc"), ("#24504C", "#24504c"), ("#24504c", "#24504c")],
)
def test_a_color_is_kept_as_lowercase_six_digit_hex(value: str, expected: str) -> None:
    assert normalize_color(value) == expected


@pytest.mark.parametrize(
    "value",
    ["24504c", "#24504", "#gggggg", "red", "rgb(0,0,0)", "#24504c80", "", None, 3, ["#fff"]],
)
def test_anything_but_hex_is_not_a_color(value: object) -> None:
    assert normalize_color(value) is None


def test_a_bad_value_is_skipped_on_its_own_and_the_rest_still_apply() -> None:
    report = _read(
        {
            "colors": {"accent": "teal", "background": "#fbf8f3"},
            "fonts": {"heading": "Comic Sans", "body": "inter"},
            "radius": "huge",
        }
    )

    assert set(_skipped(report)) == {"colors.accent", "fonts.heading", "radius"}
    assert report.theme == PracticeTheme.model_validate(
        {"colors": {"background": "#fbf8f3"}, "fonts": {"body": "Inter"}}
    )


def test_a_font_is_named_as_the_list_names_it_whatever_its_case() -> None:
    report = _read({"fonts": {"heading": "  hanken grotesk ", "body": "NUNITO SANS"}})

    assert report.theme is not None
    assert report.theme.fonts.heading == "Hanken Grotesk"
    assert report.theme.fonts.body == "Nunito Sans"


def test_unknown_keys_are_ignored_without_a_word() -> None:
    report = _read({**FULL, "logo": "logo.svg", "name": "A practice", "css": "body{}"})

    assert report.skipped == []
    assert storable_theme(report.theme) == FULL


def test_the_portals_own_colors_pass_their_own_contrast_checks() -> None:
    """Everything below leans on this: a color a theme leaves out is never the
    reason a pair fails."""
    for fg, bg in CONTRAST_PAIRS:
        assert contrast_ratio(PORTAL_COLORS[fg], PORTAL_COLORS[bg]) >= MIN_CONTRAST


def test_contrast_is_the_wcag_ratio() -> None:
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21)
    assert contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1)
    # #767676 on white is the well-known 4.54:1, just over AA.
    assert contrast_ratio("#767676", "#ffffff") == pytest.approx(4.54, abs=0.01)


@pytest.mark.parametrize(
    ("colors", "dropped"),
    [
        # the text goes, and the background it could not be read on stays
        ({"text": "#999999", "background": "#ffffff"}, {"text"}),
        # muted text only has to read on the background
        ({"mutedText": "#aaaaaa", "background": "#fbf8f3"}, {"mutedText"}),
        # the accent's own text goes; the portal's own then reads on the accent
        ({"accent": "#24504c", "accentText": "#2a5a55"}, {"accentText"}),
        # a light accent the portal's own light text cannot read on
        ({"accent": "#f0e0c0"}, {"accent"}),
        # a dark background the portal's own dark text cannot read on
        ({"background": "#1d2726"}, {"background"}),
        # a dark surface, checked against the portal's text
        ({"surface": "#333333"}, {"surface"}),
    ],
)
def test_a_pair_short_of_aa_loses_one_color(colors: dict[str, str], dropped: set[str]) -> None:
    report = _read({"colors": colors})

    assert set(_skipped(report)) == {f"colors.{name}" for name in dropped}
    assert all("contrast" in reason for reason in _skipped(report).values())
    kept = report.theme.colors.model_dump(exclude_none=True) if report.theme else {}
    assert kept == {k: v for k, v in colors.items() if k not in dropped}


def test_the_reason_names_the_color_it_was_checked_against() -> None:
    report = _read({"colors": {"mutedText": "#aaaaaa"}})

    assert _skipped(report) == {
        "colors.mutedText": "Too little contrast with background to read easily."
    }


def test_just_over_aa_passes_and_just_under_does_not() -> None:
    over = _read({"colors": {"text": "#767676", "surface": "#ffffff", "background": "#ffffff"}})
    under = _read({"colors": {"text": "#777777", "surface": "#ffffff", "background": "#ffffff"}})

    assert over.skipped == []
    assert "colors.text" in _skipped(under)


def test_a_dark_theme_that_reads_well_passes_whole() -> None:
    dark = {
        "accent": "#e8a849",
        "accentText": "#1b1815",
        "background": "#1b1815",
        "surface": "#24211d",
        "text": "#f5f1ea",
        "mutedText": "#c0b8ac",
    }
    report = _read({"colors": dark})

    assert report.skipped == []
    assert report.theme is not None
    assert storable_theme(report.theme) == {"version": 1, "colors": dark, "fonts": {}}


def test_skipping_a_color_rechecks_the_others() -> None:
    """Light text on a dark surface reads, but not on a light background: the
    text goes, and then the dark surface fails against the portal's own dark
    text, so it goes too rather than being left unreadable."""
    report = _read({"colors": {"text": "#f5f1ea", "surface": "#24211d", "background": "#f0f0f0"}})

    assert set(_skipped(report)) == {"colors.text", "colors.surface"}
    assert report.theme == PracticeTheme.model_validate({"colors": {"background": "#f0f0f0"}})


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"{not json", "valid JSON"),
        (b"\xff\xfe", "valid JSON"),
        (b"[1, 2]", "one JSON object"),
        (b'"#24504c"', "one JSON object"),
        (b'{"version": 2, "colors": {"accent": "#24504c"}}', "Use 1"),
        (b'{"version": true}', "Use 1"),
    ],
)
def test_a_theme_json_that_cannot_be_read_is_skipped_whole(data: bytes, reason: str) -> None:
    report = read_theme(data)

    assert report is not None
    assert report.theme is None
    assert [s.field for s in report.skipped] == ["theme.json"]
    assert reason in report.skipped[0].reason


def test_a_byte_order_mark_is_not_a_reason_to_refuse() -> None:
    report = read_theme(b"\xef\xbb\xbf" + json.dumps(FULL).encode())

    assert report is not None
    assert report.skipped == []


def test_groups_of_the_wrong_shape_are_skipped_whole() -> None:
    report = _read({"colors": ["#24504c"], "fonts": "Inter", "radius": 4})

    assert set(_skipped(report)) == {"colors", "fonts", "radius"}
    assert report.theme is None


def test_a_theme_with_nothing_usable_is_no_theme() -> None:
    assert _read({}).theme is None
    assert _read({"version": 1}).skipped == []
