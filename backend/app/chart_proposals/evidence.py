# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Transcript lines as a model call numbers them, and the lines a reply cites.

Shared by every call that cites the transcript by line (the proposal call,
the extraction beside the draft, the risk sections' call), so a line has the
same number in each. Kept apart from :mod:`.drafting`, which imports the
note generation service: a call the service runs can use these without
importing back into it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import Evidence

if TYPE_CHECKING:
    from collections.abc import Mapping


def segment_texts(indexed_transcript: str) -> dict[int, str]:
    texts = {}
    for line in indexed_transcript.splitlines():
        head, _, rest = line.partition("] ")
        texts[int(head.removeprefix("[S"))] = rest
    return texts


def cited_evidence(raw: Any, segments: Mapping[int, str]) -> tuple[Evidence, ...] | None:
    """The cited segments, or ``None`` when the proposal cites none or one this visit lacks."""
    if not isinstance(raw, list) or not raw:
        return None
    ids: list[int] = []
    for value in raw:
        if not isinstance(value, int) or isinstance(value, bool) or value not in segments:
            return None
        if value not in ids:
            ids.append(value)
    return tuple(Evidence(segment_id=i, text=segments[i]) for i in sorted(ids))
