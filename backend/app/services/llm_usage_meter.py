# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""LLM usage meter (THERAPY-f6eg, Phase 3b of THERAPY-bhv).

Per design doc §11.6: the metering primitive and the enforcement
primitive both live in OSS so self-hosters get the same observability
without opting into enforcement. OSS default is
``LLM_QUOTA_ENFORCEMENT=off`` — the meter still records every turn,
but :meth:`LlmUsageMeter.check_quota` always returns
:attr:`QuotaStatus.OK`.

The SaaS overlay substitutes a tier-aware ``check_quota`` (reading
limits from tenant config) without touching this module — see the
SaaS overlay's ``LlmUsageMeter`` subclass in ``pablo-saas``.

No ``tenant_id`` parameter on the public API. The practice schema is
the tenant boundary, matching :mod:`backend.app.models.chat` and
:mod:`backend.app.repositories.chat`. The design doc's nominal
``tenant_id`` signature was rationalized to OSS reality during Phase 3b.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..models import QuotaStatus
from ..utcnow import utc_now

if TYPE_CHECKING:
    from datetime import datetime

    from ..models import UsageSummary
    from ..repositories import LlmUsageRepository
    from ..settings import Settings

logger = logging.getLogger(__name__)


def period_yyyymm(when: datetime) -> str:
    """Return the ``YYYYMM`` bucket key for a UTC timestamp."""
    return f"{when.year:04d}{when.month:02d}"


class LlmUsageMeter:
    """Records LLM turn usage and answers quota checks.

    OSS implementation is observation-only: :meth:`record_turn` writes
    the monthly aggregate; :meth:`check_quota` short-circuits to
    :attr:`QuotaStatus.OK` whenever ``settings.llm_quota_enforcement``
    is not ``"on"``. SaaS overlays subclass and override
    :meth:`check_quota`.
    """

    def __init__(
        self,
        *,
        repo: LlmUsageRepository,
        settings: Settings,
    ) -> None:
        self._repo = repo
        self._settings = settings

    def record_turn(
        self,
        *,
        user_id: str,
        feature_key: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        when: datetime | None = None,
    ) -> None:
        """Increment the monthly aggregate for one completed turn.

        Failures here are swallowed — a metering hiccup must never
        propagate to the user-facing turn. The repository is the
        only place a write can fail (transient DB error), and the
        chat turn already streamed successfully by the time we get
        here, so losing one metering row is preferable to surfacing
        an error to the clinician.
        """
        recorded_at = when or utc_now()
        try:
            self._repo.record_turn(
                user_id=user_id,
                feature_key=feature_key,
                period_yyyymm=period_yyyymm(recorded_at),
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                recorded_at=recorded_at,
            )
        except Exception:
            logger.exception(
                "llm_usage_meter: failed to record turn "
                "(user_id=%s feature_key=%s model=%s)",
                user_id,
                feature_key,
                model,
            )

    def get_period_usage(
        self,
        *,
        period_start: datetime,
        period_end: datetime,
        user_id: str | None = None,
        feature_key: str | None = None,
    ) -> UsageSummary:
        """Return summed usage over an inclusive month range.

        The window is bucketed by ``YYYYMM`` regardless of the day-of-
        month component of the inputs — monthly granularity matches
        the storage tier and the customer-facing meter (§11.7).
        """
        return self._repo.get_period_usage(
            period_start_yyyymm=period_yyyymm(period_start),
            period_end_yyyymm=period_yyyymm(period_end),
            user_id=user_id,
            feature_key=feature_key,
        )

    def check_quota(
        self,
        *,
        user_id: str,
        feature_key: str,
    ) -> QuotaStatus:
        """Resolve whether the caller may proceed with a turn.

        OSS resolution order (design doc §11.6):

        1. ``settings.llm_quota_enforcement != "on"`` → ``OK``
        2. No tenant-config limits storage in OSS → ``OK``

        SaaS overlays override this method to consult tier-derived
        tenant config and return ``SOFT_WARN`` / ``HARD_BLOCK`` based
        on observed usage from :meth:`get_period_usage`.
        """
        del user_id, feature_key  # forward-compat: ignored in OSS
        if (self._settings.llm_quota_enforcement or "off").lower() != "on":
            return QuotaStatus.OK
        # Enforcement-on with no quota config = unlimited (design doc
        # §11.6 resolution order rule 2). Self-hosters who flip the
        # env on without populating limits get the same behavior as
        # leaving it off.
        return QuotaStatus.OK


__all__ = [
    "LlmUsageMeter",
    "period_yyyymm",
]
