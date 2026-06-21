# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Thin FHIR R4 read/search client over an authenticated httpx session."""

from typing import Any

import httpx

JsonDict = dict[str, Any]


class FhirClient:
    """Reads and searches FHIR resources with a bearer access token."""

    def __init__(self, base_url: str, access_token: str, client: httpx.Client) -> None:
        self._base = base_url.rstrip("/")
        self._client = client
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/fhir+json",
        }

    def read(self, resource_type: str, resource_id: str) -> JsonDict:
        """Read a single resource by id (e.g. ``Patient/abc``)."""
        response = self._client.get(
            f"{self._base}/{resource_type}/{resource_id}",
            headers=self._headers,
        )
        response.raise_for_status()
        return response.json()

    def search(self, resource_type: str, params: dict[str, str]) -> JsonDict:
        """Search a resource type, following ``next`` links into one Bundle.

        Returns a synthetic searchset Bundle whose ``entry`` list is the
        concatenation of every page, so callers see all matches at once.
        """
        entries: list[JsonDict] = []
        url: str | None = f"{self._base}/{resource_type}"
        query: dict[str, str] | None = params
        while url is not None:
            response = self._client.get(url, params=query, headers=self._headers)
            response.raise_for_status()
            bundle = response.json()
            entries.extend(bundle.get("entry", []))
            url = _next_link(bundle)
            query = None  # the next link already carries the cursor params

        return {
            "resourceType": "Bundle",
            "type": "searchset",
            "total": len(entries),
            "entry": entries,
        }


def _next_link(bundle: JsonDict) -> str | None:
    """Return the ``next`` page URL from a FHIR Bundle, if present."""
    for link in bundle.get("link", []):
        if link.get("relation") == "next":
            return link.get("url")
    return None


def fetch_capability_statement(base_url: str, client: httpx.Client) -> JsonDict:
    """Read the server's ``/metadata`` CapabilityStatement (no auth required)."""
    response = client.get(
        f"{base_url.rstrip('/')}/metadata",
        headers={"Accept": "application/fhir+json"},
    )
    response.raise_for_status()
    return response.json()
