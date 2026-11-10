# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The two copies of the Zoom room case table are the same file.

The server's ``is_zoom_room_url`` and the card's ``isZoomRoomUrl`` each run
the table. The front end keeps its own copy so it stays buildable on its own,
and a copy is only worth having if something fails when it goes stale. Edit
``backend/tests/fixtures/zoom_room_url_cases.json``, then copy it over.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SOURCE = REPO_ROOT / "backend" / "tests" / "fixtures" / "zoom_room_url_cases.json"
COPY = (
    REPO_ROOT
    / "frontend"
    / "src"
    / "lib"
    / "api"
    / "__tests__"
    / "fixtures"
    / "zoom_room_url_cases.json"
)


def test_the_front_end_copy_is_the_same_file() -> None:
    assert COPY.read_bytes() == SOURCE.read_bytes(), (
        "The front end's copy of the Zoom room cases has gone stale. Run "
        "cp -f backend/tests/fixtures/zoom_room_url_cases.json "
        "frontend/src/lib/api/__tests__/fixtures/ and commit the result."
    )
