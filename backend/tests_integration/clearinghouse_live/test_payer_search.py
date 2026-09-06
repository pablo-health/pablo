# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Payer directory search against the vendor's test mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .conftest import TEST_PAYER_ID, assert_same_shape, fixture_shape

if TYPE_CHECKING:
    from .conftest import LiveClient

_RECORDING = "payer_search_test_payer.json"


def test_the_test_payer_is_found_by_its_id(live: LiveClient) -> None:
    payers = live.adapter.search_payers(TEST_PAYER_ID)

    assert TEST_PAYER_ID in [p.primaryPayerId for p in payers]
    assert live.recorder.last_status() == 200
    assert_same_shape(live.recorder.last_json(), fixture_shape(_RECORDING))


def test_a_real_payer_is_found_by_name(live: LiveClient) -> None:
    payers = live.adapter.search_payers("Aetna")

    assert payers, "the directory returned no payers for a household name"
    assert any("aetna" in p.displayName.lower() for p in payers)
    # Each hit's ``matches`` block only carries the fields the query matched
    # on, so a different query legitimately has different keys there; the
    # payer object is the shape the adapter parses and the one held stable.
    live_payer = live.recorder.last_json()["items"][0]["payer"]
    assert_same_shape(live_payer, fixture_shape(_RECORDING)["items"][0]["payer"])
