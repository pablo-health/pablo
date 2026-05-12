# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Cloud Logging dual-write for AuditService events.

Streams each AuditLogEntry to GCP Cloud Logging under
``logName="pablo.audit_events"``. A retention-locked GCS sink ships
these to ``gs://${PROJECT_ID}-hipaa-audit-6y`` for tamper-evident
HIPAA retention (§ 164.312(c)(2) integrity protection) as
defense-in-depth on top of the canonical Postgres audit_logs table
(§ 164.312(b) audit-of-record).

Best-effort by design: Cloud Logging write failures log a warning
but never fail the request. The Postgres row is the system of
record; the Cloud Logging stream is the immutable mirror. A failing
mirror does not constitute a HIPAA audit gap so long as the canonical
write succeeded.

See ``scripts/setup-hipaa-audit-sink.sh`` for the GCP-side sink
configuration.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.audit import AuditLogEntry

logger = logging.getLogger(__name__)

CLOUD_LOG_NAME = "pablo.audit_events"


@lru_cache(maxsize=1)
def _gcp_logger():  # type: ignore[no-untyped-def]
    # Imported lazily so test/dev environments without google-cloud-logging
    # installed (or without Application Default Credentials) never trigger
    # initialization when the dual-write flag is off.
    from google.cloud import logging as gcp_logging

    return gcp_logging.Client().logger(CLOUD_LOG_NAME)


def write_to_cloud_logging(entry: AuditLogEntry) -> None:
    """Best-effort dual-write of an audit entry to Cloud Logging.

    Postgres has already persisted ``entry`` by the time this runs.
    Exceptions are logged at WARNING and swallowed — they MUST NOT
    propagate, because the request has succeeded as far as the
    audit-of-record is concerned.
    """
    try:
        payload = asdict(entry)
        _gcp_logger().log_struct(payload, severity="NOTICE")
    except Exception:
        logger.warning(
            "Cloud Logging dual-write failed for audit entry id=%s action=%s "
            "(Postgres row already persisted)",
            entry.id,
            entry.action,
            exc_info=True,
        )
