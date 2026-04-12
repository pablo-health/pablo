"""Production entrypoint for the backend server.

Reads PORT from environment (required by Cloud Run) and starts uvicorn
in single-worker mode. Used instead of shell-based CMD since distroless
images have no shell.
"""

import os

import uvicorn

uvicorn.run(
    "backend.app.main:app",
    host="0.0.0.0",  # noqa: S104 — bind all interfaces (required for Cloud Run)
    port=int(os.environ.get("PORT", "8000")),
    workers=1,
    log_level="info",
    access_log=False,
)
