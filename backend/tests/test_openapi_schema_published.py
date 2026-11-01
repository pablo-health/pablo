# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The committed OpenAPI schema stays the one the routes describe.

frontend/src/types/api/openapi.json is what the frontend's API types are
generated from. A route or model changed without regenerating it would leave
the frontend typed against a shape the backend no longer sends.
"""

from __future__ import annotations

import json

from scripts.regen_openapi_schema import ALL_ROUTES, SCHEMA_PATH, render


class TestPublishedOpenApiSchema:
    def test_the_committed_schema_matches_the_routes(self) -> None:
        assert SCHEMA_PATH.read_text(encoding="utf-8") == render(), (
            "frontend/src/types/api/openapi.json is out of date with the backend's routes. "
            "Regenerate it with `make api-schema` and commit the result."
        )

    def test_the_schema_includes_the_optionally_mounted_routes(self) -> None:
        # The render turns every optional router on; spot-check one per switch.
        paths = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["paths"]
        assert "/api/launch/intent" in paths
        assert any(p.startswith("/api/public/booking-links") for p in paths)
        assert any(p.startswith("/api/patient/chat") for p in paths)
        assert set(ALL_ROUTES) >= {"ENABLE_PATIENT_PORTAL", "PORTAL_MODULES"}
