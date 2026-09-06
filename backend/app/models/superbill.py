# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""API shapes for the superbill route.

The document itself is a PDF; the only JSON the route speaks is the refusal,
which lists what is missing so the person can fix it and try again.
"""

from __future__ import annotations

from pydantic import BaseModel

# Runtime import: Pydantic resolves the annotation when the model is built.
from .claims import FindingResponse  # noqa: TC001


class SuperbillRefusedResponse(BaseModel):
    """The ``detail`` of a 422 from the superbill route: no document was made."""

    message: str
    findings: list[FindingResponse]
