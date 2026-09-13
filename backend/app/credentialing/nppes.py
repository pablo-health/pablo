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

The registry carries a licence number against each taxonomy, and it is worth
being precise about what that is: self-reported by the provider when she
enumerated, never checked against a board, and often years old. It pre-fills a
field for her to confirm. It is not verification that anybody holds a licence —
that is primary source verification against the state board, a different job
this module does not do.

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
    #: The licence the provider recorded against her primary taxonomy. Present
    #: on many records and absent on many others, and self-reported either way
    #: — NPPES is a directory, not a licensing board, so this is a value to put
    #: in front of her, never evidence that anybody is licensed.
    license_number: str | None = None
    license_state: str | None = None
    #: NPPES marks a record ``A`` for active. A deactivated NPI still answers a
    #: lookup, so a screen that does not check this would present a retired
    #: number as though it were hers today.
    active: bool = True
    #: ``1`` for an individual, ``2`` for an organisation. Tier 0 asks for both
    #: an individual NPI and a billing NPI, so pasting the practice's type-2
    #: number into the individual field is a mistake worth catching by name
    #: rather than confirming the wrong thing and finding out at a payer.
    entity_type: int | None = None


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


def _entity_type(record: dict[str, Any]) -> int | None:
    """1 for an individual, 2 for an organisation, None when unstated.

    NPPES spells this ``NPI-1`` / ``NPI-2``. Read rather than assumed: the
    individual and billing NPI fields sit next to each other in Tier 0, and
    telling her "that is your practice's NPI, not yours" is a far better
    outcome than a confirmed record that a payer rejects months later.
    """
    raw = record.get("enumeration_type")
    if not isinstance(raw, str):
        return None
    digits = raw.strip().removeprefix("NPI-")
    return int(digits) if digits.isdigit() else None


def _to_provider(npi: str, record: dict[str, Any]) -> NppesProvider:
    raw_basic = record.get("basic")
    basic: dict[str, Any] = raw_basic if isinstance(raw_basic, dict) else {}
    taxonomy = _primary_taxonomy(record) or {}
    address = _location_address(record) or {}

    def text(source: dict[str, Any], key: str) -> str | None:
        value = source.get(key)
        return value.strip() or None if isinstance(value, str) else None

    status = basic.get("status")

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
        license_number=text(taxonomy, "license"),
        license_state=text(taxonomy, "state"),
        # Absent is treated as active: the field is not on every record, and
        # refusing to show a record because a status was missing would be a
        # worse answer than showing it.
        active=not isinstance(status, str) or status.strip().upper() != "D",
        entity_type=_entity_type(record),
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


#: How many matches to bring back. Enough that a common surname in one state is
#: usually complete, small enough that the screen stays a list she can read.
SEARCH_LIMIT = 25


def search(
    *,
    last_name: str,
    first_name: str | None = None,
    state: str | None = None,
    base_url: str | None = None,
) -> list[NppesProvider]:
    """Providers matching a name, for a clinician who cannot recall ten digits.

    State is what makes this usable. Measured against the live registry, "Jane
    Smith" returns 65 people and "Jane Smith in one state" returns one.

    Deliberately NOT filtered by taxonomy. It is tempting, since every user here
    is mental health, but the registry spreads them across at least ``Psych*``,
    ``Social Worker*``, ``Counselor*`` and ``Marriage*`` — a psych-only filter
    would hide most LCSWs, LPCs and LMFTs, which is most therapists. The
    taxonomy comes back on each result instead, where it helps her recognise
    herself rather than deciding for her whether she exists.
    """
    last_name = last_name.strip()
    if not last_name:
        return []

    params: dict[str, str | int] = {
        "version": "2.1",
        "last_name": last_name,
        "limit": SEARCH_LIMIT,
    }
    if first_name and first_name.strip():
        params["first_name"] = first_name.strip()
    if state and state.strip():
        params["state"] = state.strip().upper()

    try:
        response = httpx.get(base_url or DEFAULT_BASE_URL, params=params, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise NppesUnavailableError(str(exc)) from exc

    if not isinstance(payload, dict):
        raise NppesUnavailableError("registry returned something that is not a result set")
    if payload.get("Errors"):
        return []

    results = payload.get("results")
    if not isinstance(results, list):
        return []

    providers: list[NppesProvider] = []
    for record in results:
        if not isinstance(record, dict):
            continue
        number = record.get("number")
        # The NPI comes from the record here rather than from the caller: a
        # search does not know it in advance, and a result we cannot identify
        # is one she could never pick.
        if not isinstance(number, str | int):
            continue
        providers.append(_to_provider(str(number), record))
    return providers
