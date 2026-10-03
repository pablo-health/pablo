# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice's website against real PostgreSQL, with files on local disk.

Proven here on committed rows, through the code's own sessions:

* publishing copies the draft to a new version and makes it live; rolling back
  makes a kept version live again; tidying keeps the newest ten and the live
  one and removes every other folder; a publish that failed part-way leaves a
  number that the next publish writes from empty;
* every change is audited with the version, file count and bytes;
* a ``theme.json`` is reported on the draft, kept with the version it was
  published in, comes back with a roll back, and never stops a publish;
* which website a host serves, behind the minute-long cache (the clock is
  injected), and which file each request path gets: ``/``, a folder, a folder
  without its slash, a missing file and a traversal attempt;
* the draft's preview address, while its token works and not after.

Zip reading, path rules and the routes' permission checks are unit-tested in
``tests/test_site_files.py``, ``tests/test_site_paths.py`` and
``tests/test_practice_site_routes.py``.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from app.db import create_standalone_session
from app.models.audit import AuditAction
from app.portal.practice_site import portal_theme
from app.services.file_storage import LocalFileStorage
from app.settings import get_settings
from app.sites import public_routes
from app.sites.hosts import SiteHostCache, get_site_host_cache
from app.sites.service import RETAINED_VERSIONS, PracticeSiteService, UnknownVersionError
from app.sites.storage import SiteFileCache, get_site_file_cache
from app.sites.store import PracticeSiteStore
from app.utcnow import utc_now
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

PUBLISHER = SimpleNamespace(id="site-publisher")
SITE = {
    "index.html": b"<h1>Home</h1>",
    "about/index.html": b"<h1>About</h1>",
    "css/site.css": b"body{}",
    "404.html": b"<h1>Lost</h1>",
}


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _RecordingAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def log(self, action: Any, *_args: Any, **kwargs: Any) -> None:
        self.entries.append({"action": action, **kwargs})


@dataclass
class _Rows:
    engine: Engine
    hosts: list[str] = field(default_factory=list)
    practices: list[str] = field(default_factory=list)

    def practice(self) -> str:
        practice_id = f"site-{uuid.uuid4().hex[:10]}"
        self.practices.append(practice_id)
        return practice_id

    def host(
        self, practice_id: str, *, status: str = "active", purpose: str = "site", primary: bool
    ) -> str:
        host = f"www{uuid.uuid4().hex[:8]}.example.com"
        self.hosts.append(host)
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO platform.practice_domains "
                    "(domain, practice_id, purpose, kind, status, is_primary, created_at) "
                    "VALUES (:d, :p, :purpose, 'vanity', :status, :primary, now())"
                ),
                {
                    "d": host,
                    "p": practice_id,
                    "purpose": purpose,
                    "status": status,
                    "primary": primary,
                },
            )
        return host

    def remove_all(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM platform.practice_domains WHERE domain = ANY(:h)"),
                {"h": self.hosts},
            )
            conn.execute(
                text("DELETE FROM platform.practice_sites WHERE practice_id = ANY(:p)"),
                {"p": self.practices},
            )


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture
def rows(engine: Engine) -> Iterator[_Rows]:
    written = _Rows(engine)
    yield written
    written.remove_all()


@pytest.fixture
def bucket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A local folder standing in for the bucket, named in the settings."""
    monkeypatch.setattr(get_settings(), "practice_site_bucket", str(tmp_path))
    monkeypatch.setattr(public_routes, "site_storage", LocalFileStorage)
    return str(tmp_path)


@pytest.fixture
def audit() -> _RecordingAudit:
    return _RecordingAudit()


@pytest.fixture
def session(rows: _Rows) -> Iterator[Session]:
    """A session of the code's own, closed before ``rows`` cleans up, so a
    lock a refused change still holds never blocks the clean-up."""
    del rows
    opened = create_standalone_session()
    yield opened
    opened.rollback()
    opened.close()


def _service(
    session: Session, bucket: str, audit: _RecordingAudit | None = None
) -> PracticeSiteService:
    return PracticeSiteService(PracticeSiteStore(session), LocalFileStorage(), bucket, audit)


def _publish(
    session: Session, bucket: str, practice_id: str, files: dict[str, bytes] = SITE
) -> int:
    version = _service(session, bucket).publish_files(practice_id, files, PUBLISHER)
    session.commit()
    return version.version


def _folders(bucket: str, practice_id: str, under: str = "") -> set[str]:
    """The folders under the practice's site that still hold a file.

    A folder emptied on local disk stays behind as a directory; in a bucket
    there is no such thing, so only folders with files count.
    """
    root = Path(bucket) / "sites" / practice_id / under
    if not root.exists():
        return set()
    return {p.name for p in root.iterdir() if p.is_dir() and any(f.is_file() for f in p.rglob("*"))}


# ---------------------------------------------------------------------------
# Publishing, rolling back, tidying
# ---------------------------------------------------------------------------


def test_publishing_copies_the_draft_and_makes_it_live(
    session: Session, bucket: str, rows: _Rows, audit: _RecordingAudit
) -> None:
    practice_id = rows.practice()
    service = _service(session, bucket, audit)

    service.save_draft_files(practice_id, SITE, PUBLISHER)
    version = service.publish_draft(practice_id, PUBLISHER)
    session.commit()

    assert version.version == 1
    assert version.file_count == len(SITE)
    assert version.total_bytes == sum(len(b) for b in SITE.values())
    status = _service(session, bucket).status(practice_id)
    assert status.live_version == 1
    assert status.draft is None
    stored = Path(bucket) / "sites" / practice_id / "v1"
    assert (stored / "about" / "index.html").read_bytes() == b"<h1>About</h1>"
    assert [e["action"] for e in audit.entries] == [
        AuditAction.PRACTICE_SITE_DRAFT_SAVED,
        AuditAction.PRACTICE_SITE_PUBLISHED,
    ]
    assert audit.entries[1]["changes"] == {
        "practice_id": practice_id,
        "version": 1,
        "file_count": len(SITE),
        "total_bytes": version.total_bytes,
    }


def test_rolling_back_makes_a_kept_version_live_and_is_audited(
    session: Session, bucket: str, rows: _Rows, audit: _RecordingAudit
) -> None:
    practice_id = rows.practice()
    _publish(session, bucket, practice_id)
    _publish(session, bucket, practice_id, {"index.html": b"two"})

    _service(session, bucket, audit).roll_back(practice_id, 1, PUBLISHER)
    session.commit()

    assert _service(session, bucket).status(practice_id).live_version == 1
    (entry,) = audit.entries
    assert entry["action"] == AuditAction.PRACTICE_SITE_ROLLED_BACK
    assert entry["changes"]["version"] == 1
    assert entry["changes"]["previous_version"] == 2


def test_rolling_back_to_a_version_not_kept_is_refused(
    session: Session, bucket: str, rows: _Rows
) -> None:
    practice_id = rows.practice()
    _publish(session, bucket, practice_id)

    with pytest.raises(UnknownVersionError):
        _service(session, bucket).roll_back(practice_id, 7, PUBLISHER)


def test_tidying_keeps_the_newest_versions_and_the_live_one(
    session: Session, bucket: str, rows: _Rows
) -> None:
    practice_id = rows.practice()
    for _ in range(RETAINED_VERSIONS + 2):
        _publish(session, bucket, practice_id)
    _service(session, bucket).roll_back(practice_id, 1, PUBLISHER)
    session.commit()

    _service(session, bucket).tidy(practice_id)
    session.commit()

    kept = [v.version for v in _service(session, bucket).status(practice_id).versions]
    newest = list(range(RETAINED_VERSIONS + 2, 2, -1))
    assert kept == [*newest, 1]
    assert _folders(bucket, practice_id) == {f"v{n}" for n in [*newest, 1]}


def test_tidying_removes_a_replaced_draft(session: Session, bucket: str, rows: _Rows) -> None:
    practice_id = rows.practice()
    service = _service(session, bucket)
    service.save_draft_files(practice_id, SITE, PUBLISHER)
    service.save_draft_files(practice_id, {"index.html": b"newer"}, PUBLISHER)
    session.commit()

    service.tidy(practice_id)
    session.commit()

    (draft,) = _folders(bucket, practice_id, "draft")
    assert (Path(bucket) / "sites" / practice_id / "draft" / draft / "index.html").read_bytes() == (
        b"newer"
    )


THEMED = {
    **SITE,
    "theme.json": b'{"colors": {"accent": "#24504c", "text": "#eeeeee"}, "radius": "sm"}',
}


def test_the_draft_says_what_its_theme_gives_and_publishing_keeps_it_with_the_version(
    session: Session, bucket: str, rows: _Rows
) -> None:
    practice_id = rows.practice()
    service = _service(session, bucket)

    service.save_draft_files(practice_id, THEMED, PUBLISHER)
    session.commit()
    draft = service.status(practice_id).draft
    assert draft is not None
    assert draft.theme is not None
    assert [s.field for s in draft.theme.skipped] == ["colors.text"]

    service.publish_draft(practice_id, PUBLISHER)
    session.commit()

    (version,) = service.status(practice_id).versions
    assert version.theme is not None
    assert version.theme.colors.accent == "#24504c"
    assert version.theme.colors.text is None
    assert version.theme.radius == "sm"
    assert portal_theme(session, practice_id) == version.theme
    # The file is still part of the website, served like any other.
    assert (Path(bucket) / "sites" / practice_id / "v1" / "theme.json").exists()


def test_rolling_back_brings_back_the_theme_that_version_had(
    session: Session, bucket: str, rows: _Rows
) -> None:
    practice_id = rows.practice()
    _publish(session, bucket, practice_id, THEMED)
    _publish(session, bucket, practice_id)
    assert portal_theme(session, practice_id) is None

    _service(session, bucket).roll_back(practice_id, 1, PUBLISHER)
    session.commit()

    theme = portal_theme(session, practice_id)
    assert theme is not None
    assert theme.colors.accent == "#24504c"


def test_a_broken_theme_never_stops_a_publish(session: Session, bucket: str, rows: _Rows) -> None:
    practice_id = rows.practice()

    _publish(session, bucket, practice_id, {**SITE, "theme.json": b"{nope"})

    assert _service(session, bucket).status(practice_id).live_version == 1
    assert portal_theme(session, practice_id) is None


def test_a_header_links_to_the_practices_hosts_while_it_holds_them(
    session: Session, bucket: str, rows: _Rows
) -> None:
    practice_id = rows.practice()
    site = rows.host(practice_id, primary=True)
    portal = rows.host(practice_id, purpose="portal", primary=True)
    pending = rows.host(practice_id, status="pending", primary=False)
    header = {
        "wordmark": "Riverside Counseling",
        "links": [
            {"label": "About", "href": "/about"},
            {"label": "Fees", "href": f"https://{site}/fees"},
            {"label": "Soon", "href": f"https://{pending}/"},
        ],
        "cta": {"label": "Messages", "href": f"https://{portal}/messaging"},
    }
    service = _service(session, bucket)
    service.save_draft_files(
        practice_id, {**SITE, "theme.json": json.dumps({"header": header}).encode()}, PUBLISHER
    )
    session.commit()
    draft = service.status(practice_id).draft
    assert draft is not None
    assert draft.theme is not None
    assert [s.field for s in draft.theme.skipped] == ["header.links[2].href"]

    service.publish_draft(practice_id, PUBLISHER)
    session.commit()
    theme = portal_theme(session, practice_id)
    assert theme is not None
    assert theme.header is not None
    assert [link.label for link in theme.header.links] == ["About", "Fees"]
    assert theme.header.cta is not None

    # The portal host stops working: the call to action on it goes, the
    # version keeps it.
    with rows.engine.begin() as conn:
        conn.execute(
            text("UPDATE platform.practice_domains SET status = 'error' WHERE domain = :d"),
            {"d": portal},
        )
    session.expire_all()
    served = portal_theme(session, practice_id)
    assert served is not None
    assert served.header is not None
    assert served.header.cta is None
    assert [link.label for link in served.header.links] == ["About", "Fees"]
    (version,) = service.status(practice_id).versions
    assert version.theme is not None
    assert version.theme.header is not None
    assert version.theme.header.cta is not None


def test_a_practice_with_no_website_has_no_theme(session: Session, rows: _Rows) -> None:
    assert portal_theme(session, rows.practice()) is None


def test_a_publish_that_failed_leaves_nothing_behind_in_the_next(
    session: Session, bucket: str, rows: _Rows
) -> None:
    practice_id = rows.practice()
    service = _service(session, bucket)
    service.save_draft_files(practice_id, SITE, PUBLISHER)
    session.commit()
    # What a publish that died after writing a file leaves: a folder for the
    # next number, and no row pointing at it.
    stray = Path(bucket) / "sites" / practice_id / "v1" / "stray.html"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"half")

    assert service.publish_draft(practice_id, PUBLISHER).version == 1
    session.commit()

    assert not stray.exists()
    assert (stray.parent / "index.html").exists()


# ---------------------------------------------------------------------------
# Serving
# ---------------------------------------------------------------------------


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def client(clock: _Clock, bucket: str) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(public_routes.router)
    hosts = SiteHostCache(clock=clock)
    files = SiteFileCache()
    app.dependency_overrides[get_site_host_cache] = lambda: hosts
    app.dependency_overrides[get_site_file_cache] = lambda: files
    with TestClient(app) as test_client:
        yield test_client


def _assert_inert(response: Any) -> None:
    """These routes are reachable on the app's own origin, where an uploaded
    page must run no script and submit no form: a bare sandbox, no allow-*."""
    assert response.headers["content-security-policy"] == "sandbox; frame-ancestors 'none'"
    assert "allow-" not in response.headers["content-security-policy"]
    assert response.headers["x-robots-tag"] == "noindex"
    assert response.headers["x-content-type-options"] == "nosniff"


def _file(client: TestClient, host: str, path: str, **headers: str) -> Any:
    return client.get(
        f"/api/sites/hosts/{host}/file",
        params={"path": path},
        headers=headers,
        follow_redirects=False,
    )


def test_a_published_site_answers_on_its_hosts(
    client: TestClient, session: Session, bucket: str, rows: _Rows
) -> None:
    practice_id = rows.practice()
    primary = rows.host(practice_id, primary=True)
    alias = rows.host(practice_id, primary=False)
    _publish(session, bucket, practice_id)

    assert client.get(f"/api/sites/hosts/{primary}").json() == {
        "primary_host": primary,
        "portal_host": None,
    }
    assert client.get(f"/api/sites/hosts/{alias.upper()}:443").json() == {
        "primary_host": primary,
        "portal_host": None,
    }


def test_a_host_that_serves_no_site_is_one_404(
    client: TestClient, session: Session, bucket: str, rows: _Rows
) -> None:
    published = rows.practice()
    pending = rows.host(published, status="pending", primary=False)
    portal = rows.host(published, purpose="portal", primary=False)
    _publish(session, bucket, published)
    unpublished = rows.host(rows.practice(), primary=True)

    for host in (pending, portal, unpublished, "nobody.example.com", "127.0.0.1"):
        assert client.get(f"/api/sites/hosts/{host}").status_code == 404, host
        assert _file(client, host, "/").status_code == 404, host


def test_each_request_path_gets_its_file(
    client: TestClient, session: Session, bucket: str, rows: _Rows
) -> None:
    practice_id = rows.practice()
    host = rows.host(practice_id, primary=True)
    _publish(session, bucket, practice_id)

    home = _file(client, host, "/")
    assert (home.status_code, home.content) == (200, b"<h1>Home</h1>")
    assert home.headers["content-type"] == "text/html; charset=utf-8"
    assert home.headers["etag"] == '"v1"'
    _assert_inert(home)
    assert _file(client, host, "/about/").content == b"<h1>About</h1>"
    css = _file(client, host, "/css/site.css")
    assert css.headers["content-type"] == "text/css; charset=utf-8"

    folder = _file(client, host, "/about")
    assert (folder.status_code, folder.headers["location"]) == (301, "about/")

    missing = _file(client, host, "/nope.html")
    assert (missing.status_code, missing.content) == (404, b"<h1>Lost</h1>")
    for answer in (css, folder, missing, _file(client, "nobody.example.com", "/")):
        _assert_inert(answer)

    for attempt in ("/../index.html", "/%2e%2e/index.html", "/css/..%2f404.html", "/a\\b"):
        assert _file(client, host, attempt).content == b"<h1>Lost</h1>", attempt

    assert _file(client, host, "/", **{"If-None-Match": '"v1"'}).status_code == 304


def test_a_site_without_a_404_page_answers_a_plain_404(
    client: TestClient, session: Session, bucket: str, rows: _Rows
) -> None:
    practice_id = rows.practice()
    host = rows.host(practice_id, primary=True)
    _publish(session, bucket, practice_id, {"index.html": b"home"})

    missing = _file(client, host, "/nope")
    assert (missing.status_code, missing.text) == (404, "Not Found")


def test_a_publish_reaches_visitors_once_the_kept_answer_expires(
    client: TestClient, clock: _Clock, session: Session, bucket: str, rows: _Rows
) -> None:
    practice_id = rows.practice()
    host = rows.host(practice_id, primary=True)
    _publish(session, bucket, practice_id, {"index.html": b"one"})
    assert _file(client, host, "/").content == b"one"

    _publish(session, bucket, practice_id, {"index.html": b"two"})
    assert _file(client, host, "/").content == b"one"

    clock.now += 61
    assert _file(client, host, "/").content == b"two"


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def test_the_preview_address_serves_the_draft_until_it_is_published(
    client: TestClient, session: Session, bucket: str, rows: _Rows
) -> None:
    practice_id = rows.practice()
    service = _service(session, bucket)
    service.save_draft_files(practice_id, SITE, PUBLISHER)
    token, _expires = service.mint_preview(practice_id)
    session.commit()
    base = f"/api/practice/website/preview/{token}"

    page = client.get(f"{base}/about/")
    assert (page.status_code, page.content) == (200, b"<h1>About</h1>")
    _assert_inert(page)
    assert client.get(f"{base}/nope").content == b"<h1>Lost</h1>"
    assert client.get("/api/practice/website/preview/not-a-token/").status_code == 404

    service.publish_draft(practice_id, PUBLISHER)
    session.commit()
    assert client.get(f"{base}/").status_code == 404


def test_an_expired_preview_address_serves_nothing(
    client: TestClient, session: Session, bucket: str, rows: _Rows, engine: Engine
) -> None:
    practice_id = rows.practice()
    service = _service(session, bucket)
    service.save_draft_files(practice_id, SITE, PUBLISHER)
    token, _expires = service.mint_preview(practice_id)
    session.commit()
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE platform.practice_sites SET preview_expires_at = :t WHERE practice_id = :p"
            ),
            {"t": utc_now() - timedelta(seconds=1), "p": practice_id},
        )

    assert client.get(f"/api/practice/website/preview/{token}/").status_code == 404
