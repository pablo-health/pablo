# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice website's ``theme.json``: the few tokens its portal takes from it.

A website can carry a ``theme.json`` at its root so the practice's portal looks
like the site. It is a fixed set of tokens, never code::

    {
      "version": 1,
      "colors": {"accent": "#24504c", "accentText": "#ffffff",
                 "background": "#fbf8f3", "surface": "#ffffff",
                 "text": "#1d2726", "mutedText": "#55605e"},
      "fonts": {"heading": "Fraunces", "body": "Inter"},
      "radius": "md",
      "header": {"wordmark": "Riverside Counseling", "links": [...], "cta": {...}}
    }

* a color is ``#rgb`` or ``#rrggbb``, kept as lowercase ``#rrggbb``;
* a font is one of :data:`FONTS`, which the web app serves itself (the same
  list is in ``frontend/src/lib/portal-host/practice-theme.ts``), so a theme
  never makes a visitor's browser fetch anything from elsewhere;
* ``radius`` is one of :data:`RADII`;
* ``header`` is how the portal's header matches the website's: a wordmark,
  subtitle, links and a call to action, read by :mod:`app.sites.header`.

Each value is judged on its own: one that is wrong is skipped with a reason and
the rest still apply. Colors are then held to WCAG AA (4.5:1) in the pairs the
portal draws text with (:data:`CONTRAST_PAIRS`), each color the theme leaves out
standing in as the portal's own (:data:`PORTAL_COLORS`). In a pair that falls
short the text color is skipped if the theme gave it, else the color under it,
and the pairs are checked again until none falls short.
Keys it does not know are ignored, so a site can carry more than this reads. A
missing or unreadable ``theme.json`` never stops a website from publishing: the
portal keeps its own look.

A logo is not read yet. When it is, it belongs here as a path to an image in the
same website, sanitised before the portal shows it.

No PHI: a practice's public colors, fonts and website header.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .header import PracticeHeader, read_header

THEME_FILE = "theme.json"
THEME_VERSION = 1

ColorName = Literal["accent", "accentText", "background", "surface", "text", "mutedText"]
FontRole = Literal["heading", "body"]
Radius = Literal["none", "sm", "md", "lg"]

COLOR_NAMES: tuple[ColorName, ...] = (
    "accent",
    "accentText",
    "background",
    "surface",
    "text",
    "mutedText",
)
FONT_ROLES: tuple[FontRole, ...] = ("heading", "body")
RADII: tuple[Radius, ...] = ("none", "sm", "md", "lg")
#: The fonts a theme can name, as it names them.
FONTS: tuple[str, ...] = (
    "Fraunces",
    "Newsreader",
    "Inter",
    "Hanken Grotesk",
    "Nunito Sans",
    "Space Grotesk",
    "Poppins",
)

#: The portal's own colors, which stand in for any a theme leaves out when its
#: contrast is checked.
PORTAL_COLORS: dict[ColorName, str] = {
    "accent": "#53311c",
    "accentText": "#fef9f3",
    "background": "#fefcf8",
    "surface": "#ffffff",
    "text": "#2c1810",
    "mutedText": "#6b5344",
}
#: (foreground, background) pairs the portal draws text with.
CONTRAST_PAIRS: tuple[tuple[ColorName, ColorName], ...] = (
    ("text", "background"),
    ("text", "surface"),
    ("mutedText", "background"),
    ("accentText", "accent"),
)
#: WCAG AA for body text.
MIN_CONTRAST = 4.5

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_SHORT_HEX = re.compile(r"^#[0-9a-fA-F]{3}$")
#: An sRGB channel at or below this is on the linear part of its curve.
_SRGB_LINEAR_LIMIT = 0.04045
_FONTS_BY_KEY = {name.casefold(): name for name in FONTS}
_RADII_BY_VALUE: dict[str, Radius] = {radius: radius for radius in RADII}


class ThemeColors(BaseModel):
    """Keyed as ``theme.json`` keys them, in and out."""

    model_config = ConfigDict(frozen=True, populate_by_name=True, serialize_by_alias=True)

    accent: str | None = None
    accent_text: str | None = Field(default=None, alias="accentText")
    background: str | None = None
    surface: str | None = None
    text: str | None = None
    muted_text: str | None = Field(default=None, alias="mutedText")


class ThemeFonts(BaseModel):
    model_config = ConfigDict(frozen=True)

    heading: str | None = None
    body: str | None = None


class PracticeTheme(BaseModel):
    """A theme as it is stored and served: only values that passed."""

    model_config = ConfigDict(frozen=True)

    version: int = THEME_VERSION
    colors: ThemeColors = ThemeColors()
    fonts: ThemeFonts = ThemeFonts()
    radius: Radius | None = None
    header: PracticeHeader | None = None


class SkippedValue(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: Where in ``theme.json``, as ``colors.text``; ``theme.json`` for the whole file.
    field: str
    #: Why, in words for whoever made the website.
    reason: str


class ThemeReport(BaseModel):
    """What a website's ``theme.json`` gave the portal, and what it did not."""

    model_config = ConfigDict(frozen=True)

    #: ``None`` when nothing in it could be used.
    theme: PracticeTheme | None
    skipped: list[SkippedValue] = []


@dataclass
class _Reading:
    colors: dict[ColorName, str] = field(default_factory=dict)
    fonts: dict[FontRole, str] = field(default_factory=dict)
    radius: Radius | None = None
    header: PracticeHeader | None = None
    skipped: list[SkippedValue] = field(default_factory=list)

    def skip(self, where: str, reason: str) -> None:
        self.skipped.append(SkippedValue(field=where, reason=reason))


def normalize_color(value: object) -> str | None:
    """*value* as lowercase ``#rrggbb``, or ``None`` when it is not a hex color."""
    if not isinstance(value, str) or not _HEX.match(value):
        return None
    digits = value[1:].lower()
    if _SHORT_HEX.match(value):
        digits = "".join(c * 2 for c in digits)
    return f"#{digits}"


def _channel(value: int) -> float:
    """An sRGB channel (0-255) made linear, as WCAG's relative luminance has it."""
    c = value / 255
    return c / 12.92 if c <= _SRGB_LINEAR_LIMIT else ((c + 0.055) / 1.055) ** 2.4


def _luminance(color: str) -> float:
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(first: str, second: str) -> float:
    """The WCAG contrast ratio of two ``#rrggbb`` colors, from 1 to 21."""
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _read_colors(raw: object, reading: _Reading) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict):
        reading.skip("colors", 'Should name each color, like "accent": "#24504c".')
        return
    for name in COLOR_NAMES:
        if name not in raw:
            continue
        color = normalize_color(raw[name])
        if color is None:
            reading.skip(f"colors.{name}", "Isn't a color like #24504c.")
        else:
            reading.colors[name] = color


def _hold_to_contrast(reading: _Reading) -> None:
    """Skip a color from each pair short of :data:`MIN_CONTRAST` until none is:
    the text color when the theme gave it, else the one it is drawn on."""
    while True:
        effective = {**PORTAL_COLORS, **reading.colors}
        failing = next(
            (
                (fg, bg)
                for fg, bg in CONTRAST_PAIRS
                if (fg in reading.colors or bg in reading.colors)
                and contrast_ratio(effective[fg], effective[bg]) < MIN_CONTRAST
            ),
            None,
        )
        if failing is None:
            return
        fg, bg = failing
        name, other = (fg, bg) if fg in reading.colors else (bg, fg)
        del reading.colors[name]
        reading.skip(f"colors.{name}", f"Too little contrast with {other} to read easily.")


def _read_fonts(raw: object, reading: _Reading) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict):
        reading.skip("fonts", 'Should name each font, like "body": "Inter".')
        return
    for role in FONT_ROLES:
        if role not in raw:
            continue
        value = raw[role]
        name = _FONTS_BY_KEY.get(value.strip().casefold()) if isinstance(value, str) else None
        if name is None:
            reading.skip(
                f"fonts.{role}", f"Isn't one of the fonts the portal offers: {', '.join(FONTS)}."
            )
        else:
            reading.fonts[role] = name


def _read_radius(raw: object, reading: _Reading) -> None:
    if raw is None:
        return
    radius = _RADII_BY_VALUE.get(raw) if isinstance(raw, str) else None
    if radius is None:
        reading.skip("radius", "Should be none, sm, md or lg.")
    else:
        reading.radius = radius


def _theme(reading: _Reading) -> PracticeTheme | None:
    if not (reading.colors or reading.fonts or reading.radius or reading.header):
        return None
    return PracticeTheme(
        colors=ThemeColors.model_validate(reading.colors),
        fonts=ThemeFonts(**reading.fonts),
        radius=reading.radius,
        header=reading.header,
    )


def _unreadable(reason: str) -> ThemeReport:
    return ThemeReport(theme=None, skipped=[SkippedValue(field=THEME_FILE, reason=reason)])


def read_theme(
    data: bytes | None,
    hosts: frozenset[str] = frozenset(),
    brands: tuple[str, ...] | None = None,
) -> ThemeReport | None:
    """The theme in a website's ``theme.json`` bytes; ``None`` when it has none.

    *hosts* are the practice's own, the only ones a header link may name in
    full (:mod:`app.sites.header`); with none, only paths on the website pass.
    *brands* are the names a header may not take, the deployment's by default.
    """
    if data is None:
        return None
    try:
        raw: Any = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return _unreadable("Isn't valid JSON.")
    if not isinstance(raw, dict):
        return _unreadable("Should be one JSON object.")
    version = raw.get("version", THEME_VERSION)
    if version != THEME_VERSION or isinstance(version, bool):
        return _unreadable(f"Version {version!r} isn't one the portal reads. Use 1.")
    reading = _Reading()
    _read_colors(raw.get("colors"), reading)
    _hold_to_contrast(reading)
    _read_fonts(raw.get("fonts"), reading)
    _read_radius(raw.get("radius"), reading)
    reading.header = read_header(raw.get("header"), hosts, reading.skip, brands)
    return ThemeReport(theme=_theme(reading), skipped=reading.skipped)


def stored_theme(value: dict[str, Any] | None) -> PracticeTheme | None:
    """A theme as stored with a site version, read back."""
    return PracticeTheme.model_validate(value) if value else None


def storable_theme(theme: PracticeTheme | None) -> dict[str, Any] | None:
    """*theme* as it is stored: only the values that passed."""
    return theme.model_dump(exclude_none=True) if theme else None
