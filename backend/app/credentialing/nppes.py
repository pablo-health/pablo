# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Look a clinician up in the NPPES registry, by her own NPI.

NPPES is the public NPI registry CMS publishes. It needs no key and carries no
PHI — every record in it is already public, and the one we fetch is the
clinician's own — so this is an ordinary outbound read rather than anything
that has to be audited or encrypted.

It exists to make the confirm tier honest. A card that says "Nothing on file"
and asks her to confirm it is asking her to agree with a blank; a card that
says "Test Therapist, LCSW, 101YM0800X — from the NPPES registry" is asking her
the question the tier was designed around. What comes back is never written
to her record here: it is presented, she confirms it, and the confirmation is
what promotes it. That order is the whole design, and a lookup that wrote
directly would quietly undo it.

The registry disagreeing with her is a real and common outcome — people move
practices and the registry lags — so "not found" and "found something
different" are ordinary results with screens of their own, not errors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

#: The public registry. CMS documents the `version` parameter as required and
#: the `number` search as an exact match on a single NPI.
DEFAULT_BASE_URL = "https://npiregistry.cms.hhs.gov/api/"

#: Short on purpose. This runs inside a request a clinician is waiting on, and
#: a registry that is slow today is not a reason to hold her setup open — the
#: screen falls back to asking her, which it can already do.
TIMEOUT_SECONDS = 5.0

#: An NPI is ten digits. Checked before we call rather than after, so a typo
#: costs nothing and the registry is not asked to validate our input.
NPI_LENGTH = 10


class NppesUnavailableError(Exception):
    """The registry could not be reached or did not answer usefully.

    Distinct from "no such NPI", which is a finding rather than a failure: one
    means try again or type it yourself, the other means the number is wrong.
    """


@dataclass(frozen=True)
class NppesProvider:
    """What the registry knows, in the shape the confirm cards present.

    Every field is optional because the registry's own coverage is uneven — a
    record can carry a taxonomy and no practice address, or an organisation
    name and no credential. A missing field renders as nothing on file, which
    is true.
    """

    npi: str
    legal_name: str | None = None
    credential: str | None = None
    taxonomy_code: str | None = None
    taxonomy_description: str | None = None
    address_line1: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None


def is_well_formed(npi: str) -> bool:
    """Whether this could be an NPI at all. Shape only — not a checksum."""
    return len(npi.strip()) == NPI_LENGTH and npi.strip().isdigit()


def _primary_taxonomy(record: dict[str, Any]) -> dict[str, Any] | None:
    """The taxonomy the provider nominated, falling back to the first listed.

    A record can carry several. The primary flag is the provider's own answer
    to "what do you mainly do", which is the one a payer wants on a claim.
    """
    taxonomies = record.get("taxonomies") or []
    if not isinstance(taxonomies, list):
        return None
    primary = next((t for t in taxonomies if isinstance(t, dict) and t.get("primary")), None)
    if primary is not None:
        return primary
    return next((t for t in taxonomies if isinstance(t, dict)), None)


def _location_address(record: dict[str, Any]) -> dict[str, Any] | None:
    """The practice location, not the mailing address.

    NPPES carries both and they are often different — a mailing address can be
    a PO box or an old billing service. The location is where she practises,
    which is what a payer application asks for.
    """
    addresses = record.get("addresses") or []
    if not isinstance(addresses, list):
        return None
    location = next(
        (a for a in addresses if isinstance(a, dict) and a.get("address_purpose") == "LOCATION"),
        None,
    )
    if location is not None:
        return location
    return next((a for a in addresses if isinstance(a, dict)), None)


def _person_name(basic: dict[str, Any]) -> str | None:
    """Her name as the registry spells it, or the organisation's.

    Built from the parts rather than read from a single field: NPPES has no
    one "display name", and an individual record carries first/last while an
    organisation record carries only the organisation name.
    """
    organization = basic.get("organization_name")
    if isinstance(organization, str) and organization.strip():
        return organization.strip()
    parts = [basic.get("first_name"), basic.get("middle_name"), basic.get("last_name")]
    name = " ".join(str(p).strip() for p in parts if isinstance(p, str) and p.strip())
    return name or None


def _to_provider(npi: str, record: dict[str, Any]) -> NppesProvider:
    raw_basic = record.get("basic")
    basic: dict[str, Any] = raw_basic if isinstance(raw_basic, dict) else {}
    taxonomy = _primary_taxonomy(record) or {}
    address = _location_address(record) or {}

    def text(source: dict[str, Any], key: str) -> str | None:
        value = source.get(key)
        return value.strip() or None if isinstance(value, str) else None

    return NppesProvider(
        npi=npi,
        legal_name=_person_name(basic),
        credential=text(basic, "credential"),
        taxonomy_code=text(taxonomy, "code"),
        taxonomy_description=text(taxonomy, "desc"),
        address_line1=text(address, "address_1"),
        city=text(address, "city"),
        state=text(address, "state"),
        postal_code=text(address, "postal_code"),
    )


def look_up(
    npi: str,
    *,
    base_url: str | None = None,
    timeout: float = TIMEOUT_SECONDS,
) -> NppesProvider | None:
    """The registry's record for this NPI, or ``None`` if it has no such number.

    Raises :class:`NppesUnavailableError` when the registry cannot be reached
    or answers with something that is not a result set. The caller is expected
    to tell those apart: not-found means her number is wrong, unavailable means
    try later or type it in.
    """
    npi = npi.strip()
    if not is_well_formed(npi):
        return None

    url = base_url or DEFAULT_BASE_URL
    try:
        response = httpx.get(
            url,
            params={"version": "2.1", "number": npi},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise NppesUnavailableError(str(exc)) from exc

    if not isinstance(payload, dict):
        raise NppesUnavailableError("registry returned something that is not a result set")

    # NPPES answers a bad request with 200 and an Errors key rather than a
    # status code, so the shape has to be checked rather than the response.
    if payload.get("Errors"):
        return None

    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return None
    first = results[0]
    if not isinstance(first, dict):
        raise NppesUnavailableError("registry returned a result that is not a record")

    return _to_provider(npi, first)
