"""Best-effort extraction of one JSON object from model text.

Models asked for JSON sometimes wrap it in a ```json fence or surround it with
prose. ``extract_json_object`` recovers the object from a bare string, a fenced
block, or text with a balanced ``{...}`` span embedded in it, and never raises
— callers degrade on ``None`` rather than catching a decode error.

Pure: no network, no model types.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json_object(raw: str | None) -> dict[str, Any] | None:
    """Return the first JSON object parseable from *raw*, or ``None``.

    Tries, in order: the whole string, any ```` ```json ```` fenced blocks, and
    the first balanced ``{...}`` span. The first candidate that parses to a
    ``dict`` wins. Never raises.
    """
    if not raw or not isinstance(raw, str):
        return None
    candidates: list[str] = [raw.strip()]
    candidates.extend(m.strip() for m in _FENCE_RE.findall(raw))
    brace_span = _first_balanced_object(raw)
    if brace_span is not None:
        candidates.append(brace_span)
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except (ValueError, RecursionError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _first_balanced_object(text: str) -> str | None:
    """Return the first balanced ``{...}`` span, string-aware."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None
