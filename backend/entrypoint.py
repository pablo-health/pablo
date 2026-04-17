"""Production entrypoint for the backend server.

Reads PORT from environment (required by Cloud Run) and starts uvicorn
in single-worker mode. Used instead of shell-based CMD since distroless
images have no shell.

Optional observability (env-var-gated, no-op if unconfigured):
  NEW_RELIC_LICENSE_KEY → Python APM agent
  SENTRY_DSN            → Sentry error tracking
Both are initialized before uvicorn imports the app so auto-instrumentation
has a chance to patch.
"""

import logging
import os

logger = logging.getLogger(__name__)


def _init_new_relic() -> None:
    """Initialize New Relic APM if a license key is configured.

    Safe to call regardless of whether `newrelic` is installed — we skip
    silently if the import fails. Must run before FastAPI is imported so
    the agent can patch WSGI/ASGI middlewares.
    """
    if not os.environ.get("NEW_RELIC_LICENSE_KEY"):
        return
    try:
        import newrelic.agent  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError:
        logger.warning(
            "NEW_RELIC_LICENSE_KEY is set but `newrelic` is not installed; "
            "skipping APM init. Add `newrelic` to pyproject main deps to enable."
        )
        return
    config_file = os.environ.get("NEW_RELIC_CONFIG_FILE")
    env = os.environ.get("NEW_RELIC_ENVIRONMENT")
    newrelic.agent.initialize(config_file, env)
    logger.info("New Relic APM initialized")


def _init_sentry() -> None:
    """Initialize Sentry if SENTRY_DSN is configured. PHI-safe defaults."""
    if not os.environ.get("SENTRY_DSN"):
        return
    try:
        import sentry_sdk  # type: ignore[import-not-found]  # noqa: PLC0415
        from sentry_sdk.integrations.fastapi import (  # type: ignore[import-not-found]  # noqa: PLC0415
            FastApiIntegration,
        )
        from sentry_sdk.integrations.starlette import (  # type: ignore[import-not-found]  # noqa: PLC0415
            StarletteIntegration,
        )
    except ImportError:
        logger.warning(
            "SENTRY_DSN is set but `sentry-sdk` is not installed; skipping. "
            "Add `sentry-sdk[fastapi]` to pyproject main deps to enable."
        )
        return
    sentry_sdk.init(
        dsn=os.environ["SENTRY_DSN"],
        # PHI-safety: defaults err on the side of NOT capturing request bodies /
        # PII. Without a BAA with Sentry you MUST keep these off.
        send_default_pii=False,
        max_request_body_size="never",
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            StarletteIntegration(transaction_style="endpoint"),
        ],
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.05")),
        environment=os.environ.get("ENVIRONMENT", "production"),
        release=os.environ.get("PABLO_VERSION", "unknown"),
    )
    logger.info("Sentry initialized (PII scrubbing on, traces at 5%% default)")


_init_new_relic()
_init_sentry()

import uvicorn  # noqa: E402 — must follow observability init

uvicorn.run(
    "backend.app.main:app",
    host="0.0.0.0",  # noqa: S104 — bind all interfaces (required for Cloud Run)
    port=int(os.environ.get("PORT", "8000")),
    workers=1,
    log_level="info",
    access_log=False,
)
