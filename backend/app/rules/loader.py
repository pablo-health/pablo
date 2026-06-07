# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Load rulesets from JSON data files.

Ruleset contents are data, not code: a deployment supplies its own
effective-dated JSON files and loads them with :func:`load_ruleset`. The
on-disk shape mirrors the model fields one-to-one, so a loaded ruleset
round-trips back to an equivalent JSON document.

Expected JSON shape::

    {
      "id": "EXAMPLE-2026.06",
      "version": "2026.06",
      "effective_date": "2026-06-01",
      "items": [
        {
          "id": "item-1",
          "applies_when": {
            "provider_type": ["prescriber"],
            "state": ["XX"]
          },
          "authority_ref": "Example Ref 1",
          "metadata": {"cadence_days": 365}
        }
      ]
    }

Any ``applies_when`` dimension that is absent (or explicitly ``null``)
is treated as ``None`` ("any"); present dimensions are read as lists and
stored as tuples.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from .models import AppliesWhen, RuleItem, Ruleset

_DIMENSIONS = ("provider_type", "state", "schedule", "drug_class")


def _parse_applies_when(raw: dict[str, Any] | None) -> AppliesWhen:
    """Build an ``AppliesWhen`` from a raw mapping, defaulting to "any"."""

    raw = raw or {}
    kwargs: dict[str, tuple[str, ...] | None] = {}
    for dimension in _DIMENSIONS:
        value = raw.get(dimension)
        kwargs[dimension] = tuple(value) if value is not None else None
    return AppliesWhen(**kwargs)


def _parse_item(raw: dict[str, Any]) -> RuleItem:
    return RuleItem(
        id=raw["id"],
        applies_when=_parse_applies_when(raw.get("applies_when")),
        authority_ref=raw.get("authority_ref"),
        metadata=raw.get("metadata"),
    )


def load_ruleset(path: str | Path) -> Ruleset:
    """Load and parse a ruleset from a JSON file at ``path``.

    The returned ``Ruleset`` is structurally equivalent to the source
    document (see :func:`dump_ruleset` for the inverse).
    """

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Ruleset(
        id=data["id"],
        version=data["version"],
        effective_date=date.fromisoformat(data["effective_date"]),
        items=[_parse_item(item) for item in data.get("items", [])],
    )


def _dump_applies_when(applies_when: AppliesWhen) -> dict[str, list[str]]:
    """Serialize an ``AppliesWhen``, omitting "any" (``None``) dimensions."""

    out: dict[str, list[str]] = {}
    for dimension in _DIMENSIONS:
        value: tuple[str, ...] | None = getattr(applies_when, dimension)
        if value is not None:
            out[dimension] = list(value)
    return out


def _dump_item(item: RuleItem) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": item.id,
        "applies_when": _dump_applies_when(item.applies_when),
    }
    if item.authority_ref is not None:
        out["authority_ref"] = item.authority_ref
    if item.metadata is not None:
        out["metadata"] = item.metadata
    return out


def dump_ruleset(ruleset: Ruleset) -> dict[str, Any]:
    """Serialize a ``Ruleset`` to a JSON-compatible mapping.

    The result, written with :func:`json.dump`, parses back to an
    equivalent ``Ruleset`` via :func:`load_ruleset`.
    """

    return {
        "id": ruleset.id,
        "version": ruleset.version,
        "effective_date": ruleset.effective_date.isoformat(),
        "items": [_dump_item(item) for item in ruleset.items],
    }
