# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Pytest setup for the eval suite.

Loads backend/evals/.env (if present) so BRAINTRUST_API_KEY and
related vars are available without exporting them in the shell.

The eval suite is intentionally isolated from the main backend tests
(which mock the database and Firebase). Eval tests only need network
access to Braintrust + the proxy; no app fixtures.
"""

from pathlib import Path

from dotenv import load_dotenv

EVALS_ENV = Path(__file__).parent / ".env"
if EVALS_ENV.exists():
    load_dotenv(EVALS_ENV)
