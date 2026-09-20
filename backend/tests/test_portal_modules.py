# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The portal's capability document is an intersection, and it may only narrow.

Three things have to hold together, and the third is the one that turns the
first two from a claim into a fact:

1. a module the deployment did not configure is off;
2. a module this build does not mount is off, however it is configured;
3. an unconfigured module's routes are NOT THERE — so "the shell doesn't
   show it" is never the only thing standing in front of one.

(3) is why the mounted half is read off the assembled route table rather
than off the setting. A test that asserted the setting against itself would
pass on the day somebody stopped mounting from it.
"""

from __future__ import annotations

from app.main import portal_module_routers
from app.portal.modules import (
    MODULE_MARKER_PATHS,
    PORTAL_MODULE_NAMES,
    known_modules,
    mounted_modules,
    mounted_modules_on,
    portal_capabilities,
)
from app.settings import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient

ALL_MOUNTED = frozenset(PORTAL_MODULE_NAMES)


class TestTheConfiguredListIsParsed:
    def test_a_comma_separated_string_becomes_names(self) -> None:
        """``PORTAL_MODULES=intake,messaging`` has to mean what it says.

        Pydantic-settings parses a ``list[str]`` field from the environment
        as JSON, so the obvious spelling of this setting would have failed
        to load rather than working. Hence the string plus a property.
        """
        settings = Settings(portal_modules="intake, messaging ,APPOINTMENTS")
        assert settings.portal_module_names == ("intake", "messaging", "appointments")

    def test_blanks_and_repeats_collapse(self) -> None:
        settings = Settings(portal_modules="intake,,intake, ,messaging")
        assert settings.portal_module_names == ("intake", "messaging")

    def test_the_default_is_the_three_modules_that_exist(self) -> None:
        """Pinned, because it decides what a deployment gets with no config.

        All three have a patient-facing router today, and a section of the
        portal to reach. ``documents`` is deliberately absent even though
        its routes are mounted: they are the seam the other modules upload
        through, not a section of their own, so naming it here would
        promise a navigation item that leads nowhere. ``billing`` is absent
        because its patient-facing half has not been built at all.
        """
        assert Settings().portal_module_names == ("intake", "messaging", "appointments")

    def test_an_unknown_name_is_dropped_not_refused(self) -> None:
        """A rollback to an older image must not fail to boot.

        The older image does not know the newer image's module name. Ignoring
        it costs nothing; refusing it would take the deployment down.
        """
        assert known_modules(["intake", "telepathy"]) == ("intake",)


class TestTheIntersection:
    def test_configured_and_mounted_is_on(self) -> None:
        caps = portal_capabilities(configured=["intake"], mounted=ALL_MOUNTED)
        assert caps["intake"] is True

    def test_configured_but_not_mounted_is_off(self) -> None:
        """The half that makes the document honest rather than aspirational.

        A navigation item that leads to a 404 is worse than no navigation
        item, so what is actually served wins over what was asked for.
        """
        caps = portal_capabilities(configured=["intake"], mounted=frozenset())
        assert caps["intake"] is False

    def test_mounted_but_not_configured_is_off(self) -> None:
        caps = portal_capabilities(configured=[], mounted=ALL_MOUNTED)
        assert all(value is False for value in caps.values())

    def test_a_narrowed_list_removes_a_module(self) -> None:
        """What a layered deployment is allowed to do: pass fewer names."""
        wide = portal_capabilities(configured=["intake", "messaging"], mounted=ALL_MOUNTED)
        narrowed = portal_capabilities(configured=["intake"], mounted=ALL_MOUNTED)
        assert wide["messaging"] is True
        assert narrowed["messaging"] is False

    def test_a_widened_list_cannot_add_one(self) -> None:
        """What it is NOT allowed to do, and why it structurally cannot.

        Naming every module in the world does not put a route in the table,
        and the table is the other half of the intersection.
        """
        caps = portal_capabilities(
            configured=list(PORTAL_MODULE_NAMES), mounted=frozenset({"intake"})
        )
        assert caps["intake"] is True
        assert [name for name, on in caps.items() if on] == ["intake"]

    def test_every_known_module_is_reported_even_when_off(self) -> None:
        """A client can tell "off" from "never heard of it"."""
        caps = portal_capabilities(configured=[], mounted=frozenset())
        assert set(caps) == set(PORTAL_MODULE_NAMES)


class TestMountedIsReadFromTheRouteTable:
    def test_a_marker_path_present_means_mounted(self) -> None:
        assert mounted_modules([MODULE_MARKER_PATHS["intake"]]) == frozenset({"intake"})

    def test_nothing_present_means_nothing_mounted(self) -> None:
        assert mounted_modules(["/api/health", "/api/patients"]) == frozenset()

    def test_the_real_app_reports_the_modules_it_serves(self) -> None:
        """The markers must name paths that exist, or a module reads as off forever.

        A typo fails safe — the capability goes off — which is exactly the
        kind of safe failure nobody notices. So it is asserted against the
        assembled application for the three modules that have a
        patient-facing surface today.

        ``documents`` is mounted and is NOT in the default configured list,
        which is the deliberate middle state: the patient-facing routes
        exist, because sending in an insurance card and attaching a file to
        a message both upload through them, but there is no Documents
        section in the portal for somebody to open. So it reports off in the
        capability document until a deployment names it — which is the
        mounted-but-unconfigured case the intersection tests above cover.

        ``billing`` is asserted absent on purpose: a real portal module
        whose patient-facing half has not been built, so it reports off
        however it is configured. The day it lands, this is where somebody
        has to come back.

        ``chat`` is mounted here because the test settings turn
        ``ENABLE_PATIENT_CHAT`` on, which is the gate that decides whether
        this build serves patient chat at all. Naming it in
        ``PORTAL_MODULES`` is the separate, narrower decision about whether
        the portal offers it — so a mounted-but-unconfigured chat reports
        off in the capability document, which the intersection cases above
        cover.
        """
        from app.main import app  # noqa: PLC0415 — the assembled app is the subject

        mounted = mounted_modules_on(app)
        assert {"intake", "messaging", "appointments", "documents"} <= mounted
        assert "billing" not in mounted
        # Mounted, and still off: the portal offers no Documents section.
        assert "documents" not in Settings().portal_module_names

    def test_reading_the_raw_route_list_would_find_nothing(self) -> None:
        """Why ``mounted_modules_on`` exists rather than a walk of ``app.routes``.

        fastapi 0.137 turned ``app.routes`` into a tree, so the naive walk
        reports only the few routes declared on the application object and
        misses every included router. Pinned, because the wrong version of
        this reads perfectly and turns every module off.
        """
        from app.main import app  # noqa: PLC0415

        naive = mounted_modules(getattr(route, "path", "") for route in app.routes)
        assert naive == frozenset()
        assert mounted_modules_on(app) != naive


class TestAnUnconfiguredModuleIsNotMounted:
    """The claim that frontend hiding is not the authorization control.

    ``portal_module_routers`` is the one place the setting turns into
    routers, so mounting its output on a bare app is the same operation
    ``app.main`` performs — not a re-implementation of it.
    """

    def test_only_the_named_modules_produce_routers(self) -> None:
        routers = portal_module_routers(["intake", "messaging"])
        paths = {route.path for router in routers for route in router.routes}
        assert MODULE_MARKER_PATHS["intake"] in paths
        assert MODULE_MARKER_PATHS["messaging"] in paths
        assert MODULE_MARKER_PATHS["appointments"] not in paths

    def test_an_unlisted_modules_path_answers_404(self) -> None:
        app = FastAPI()
        for router in portal_module_routers(["intake", "messaging"]):
            app.include_router(router)

        client = TestClient(app)
        assert client.get(MODULE_MARKER_PATHS["appointments"]).status_code == 404

    def test_naming_nothing_mounts_nothing(self) -> None:
        assert portal_module_routers([]) == []

    def test_the_round_trip_agrees_with_the_intersection(self) -> None:
        """Mount from a list, read it back off the table, get the list.

        This is the property the whole design rests on: the capability
        document and the mounting are the same fact seen twice.
        """
        configured = ["intake", "messaging"]
        app = FastAPI()
        for router in portal_module_routers(configured):
            app.include_router(router)

        caps = portal_capabilities(configured=configured, mounted=mounted_modules_on(app))
        assert [name for name, on in caps.items() if on] == configured
