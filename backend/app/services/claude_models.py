# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What each Claude model accepts, in one place.

Whether a request may carry ``temperature`` or force a tool call depends on
the model, not on the endpoint that serves it: a model id reads the same on
Vertex and on Bedrock, apart from the wrapping (``claude-sonnet-5-5``,
``us.anthropic.claude-sonnet-5-5-v1:0``, ``claude-haiku-4-5@20251001``). Every
gateway that calls Claude asks here instead of carrying its own check on a
model name.

Measured on Vertex AI, 2026-11-05, with one small request per model and
parameter:

====================  ===============  =======================
model                 ``temperature``  forced ``tool_choice``
====================  ===============  =======================
claude-haiku-5-5      rejected (400)   accepted
claude-sonnet-5-5     rejected (400)   rejected (400)
claude-opus-5-5       rejected (400)   rejected (400)
====================  ===============  =======================

``tool_choice: auto`` was accepted by all three. Earlier models were not
measured, and a model this module cannot place is treated as accepting
everything, which is what every gateway did before. Where a later 5.x model
turns out to differ, this table is the one place to change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ``claude-<tier>-<major>[-<minor>]`` anywhere in the id. A minor is one or
# two digits not followed by another digit, so a dated id such as
# ``claude-haiku-4-5-20251001-v1:0`` reads as 4.5 and not 4.520251001.
_MODEL_ID = re.compile(
    r"claude-(?P<tier>haiku|sonnet|opus|fable)-(?P<major>\d+)(?:-(?P<minor>\d{1,2})(?!\d))?"
)

#: The first major version whose models reject ``temperature``.
_NO_SAMPLING_FROM_MAJOR = 5


@dataclass(frozen=True)
class ClaudeCapabilities:
    """The request parameters a Claude model accepts."""

    accepts_temperature: bool = True
    """Whether ``temperature`` may be sent. When False it must be left out."""

    forces_tool_choice: bool = True
    """Whether ``tool_choice`` may name a tool (or be ``any``). When False,
    ask for the tool with ``auto`` and say so in the prompt."""


def capabilities_for(model: str) -> ClaudeCapabilities:
    """The capabilities of ``model``, given as any provider's id for it."""
    match = _MODEL_ID.search(model)
    if match is None:
        return ClaudeCapabilities()
    major = int(match["major"])
    if major < _NO_SAMPLING_FROM_MAJOR:
        return ClaudeCapabilities()
    return ClaudeCapabilities(
        accepts_temperature=False,
        # Haiku 5.5 accepts a forced tool call; Sonnet 5.5 and Opus 5.5 do not.
        forces_tool_choice=match["tier"] == "haiku",
    )


def sampling_kwargs(model: str, temperature: float) -> dict[str, float]:
    """``{"temperature": temperature}``, or ``{}`` for a model that rejects it."""
    return {"temperature": temperature} if capabilities_for(model).accepts_temperature else {}


__all__ = ["ClaudeCapabilities", "capabilities_for", "sampling_kwargs"]
