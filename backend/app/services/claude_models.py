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

``tool_choice: auto`` was accepted by all three.

What is measured and what is inferred:

- The table above is measured, on Vertex. Nothing was measured on Bedrock,
  where the same rule is applied on the strength of it.
- Every other model in the 5 family (``claude-fable-5-*``, a later 5.x, a
  bare ``claude-sonnet-5``) is placed by inference from the 5.5 rule: no
  ``temperature``, and a forced tool only for Haiku. That errs the safe way:
  leaving a parameter out never draws a 400.
- Models before the 5 family were not measured and are treated as accepting
  everything, which is what every gateway did before.
- A Claude id this module cannot place (an alias such as
  ``claude-opus-latest``) is treated the same way and logged once.

Where a later model turns out to differ, this table is the one place to change.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ``claude-<tier>-<major>[-<minor>]`` anywhere in the id. A minor is one or
# two digits not followed by another digit, so a dated id such as
# ``claude-haiku-4-5-20251001-v1:0`` reads as 4.5 and not 4.520251001.
_MODEL_ID = re.compile(
    r"claude-(?P<tier>haiku|sonnet|opus|fable)-(?P<major>\d+)(?:-(?P<minor>\d{1,2})(?!\d))?",
    re.IGNORECASE,
)

#: The first major version whose models reject ``temperature``.
_NO_SAMPLING_FROM_MAJOR = 5

#: Ids already reported as unplaceable, so a busy path logs each once.
_reported_unplaced: set[str] = set()

#: The tool's description when the call forces it, and when it only asks for it.
FORCED_TOOL_DESCRIPTION = "Return the result as structured output."
REQUESTED_TOOL_DESCRIPTION = "Always call this tool to return the requested structured result."


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
        if "claude" in model.lower() and model not in _reported_unplaced:
            _reported_unplaced.add(model)
            # The model id only: it names a model, never a person or a prompt.
            logger.warning(
                "Claude model id %r is not one claude_models can place; "
                "assuming it accepts every request parameter",
                model,
            )
        return ClaudeCapabilities()
    major = int(match["major"])
    if major < _NO_SAMPLING_FROM_MAJOR:
        return ClaudeCapabilities()
    return ClaudeCapabilities(
        accepts_temperature=False,
        # Haiku 5.5 accepts a forced tool call; Sonnet 5.5 and Opus 5.5 do not.
        forces_tool_choice=match["tier"].lower() == "haiku",
    )


def sampling_kwargs(model: str, temperature: float) -> dict[str, float]:
    """``{"temperature": temperature}``, or ``{}`` for a model that rejects it."""
    return {"temperature": temperature} if capabilities_for(model).accepts_temperature else {}


def tool_description(model: str) -> str:
    """The description to give the structured-output tool for ``model``."""
    if capabilities_for(model).forces_tool_choice:
        return FORCED_TOOL_DESCRIPTION
    return REQUESTED_TOOL_DESCRIPTION


def tool_instruction(user_prompt: str, tool_name: str) -> str:
    """``user_prompt`` with the instruction to call ``tool_name`` added."""
    return f"{user_prompt}\n\nCall {tool_name} with the result."


def with_tool_instruction(model: str, user_prompt: str, tool_name: str) -> str:
    """``user_prompt``, told to call ``tool_name`` when ``model`` cannot be forced to.

    A model that rejects a forced tool call is asked for the tool with
    ``auto`` instead, and without this line it may answer in plain text.
    """
    if capabilities_for(model).forces_tool_choice:
        return user_prompt
    return tool_instruction(user_prompt, tool_name)


__all__ = [
    "FORCED_TOOL_DESCRIPTION",
    "REQUESTED_TOOL_DESCRIPTION",
    "ClaudeCapabilities",
    "capabilities_for",
    "sampling_kwargs",
    "tool_description",
    "tool_instruction",
    "with_tool_instruction",
]
