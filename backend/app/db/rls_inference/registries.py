# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a deployment registered, handed to the planner instead of read from globals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .owner import OwnerScope


@dataclass(frozen=True)
class Registries:
    """The registrations the planner needs, as ``app.db`` holds them at call time.

    ``app.db`` keeps the registries themselves, filled at bootstrap by the
    ``register_overlay_*`` functions, and builds one of these per call. The
    planner reads nothing else: no module-global state.
    """

    #: ``register_overlay_owner_scoped``: table -> how it names its owner.
    owner_scoped: Mapping[str, OwnerScope]
    #: Tables the patient principal may read -> the column naming the patient.
    patient_readable: Mapping[str, str]
    #: Tables the patient principal may also write -> the same column.
    patient_writable: Mapping[str, str]
