# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading the public NPI registry, and the shapes it answers in.

The registry is what turns the confirm tier from "agree with this blank" into
"is this you". Everything here is about reading it honestly:

  * "no such NPI" is a finding, not a failure. People mistype ten digits, and
    the screen that says "check the number" is a different screen from the one
    that says "try again later". Conflating them sends her to the wrong fix.
  * NPPES answers a malformed request with HTTP 200 and an ``Errors`` key, so
    the payload has to be read rather than the status code.
  * the registry's coverage is uneven — a record can carry a taxonomy and no
    address, or an organisation name and no credential. A missing field is
    nothing on file, not a crash.
  * the LOCATION address, never the MAILING one: a mailing address is often a
    PO box or an old billing service, and a payer application wants where she
    actually practises.
  * the primary taxonomy, because that is the provider's own answer to "what
    do you mainly do" and the one a payer expects on a claim.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.credentialing import nppes


def _payload(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "basic": {
            "first_name": "TEST",
            "last_name": "THERAPIST",
            "credential": "LCSW",
        },
        "taxonomies": [
            {"code": "101YM0800X", "desc": "Mental Health Counselor", "primary": True},
        ],
        "addresses": [
            {
                "address_purpose": "LOCATION",
                "address_1": "14 MILL STREET",
                "city": "DURHAM",
                "state": "NC",
                "postal_code": "27701",
            },
        ],
    }
    record.update(overrides)
    return {"result_count": 1, "results": [record]}


def _transport(handler: Any) -> Any:
    """Patch httpx.get with a stand-in. Nothing here touches the network."""
    return handler


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Answer the next lookup with whatever a test sets on ``.payload``."""

    class Registry:
        payload: Any = _payload()
        status_code: int = 200
        raises: Exception | None = None
        last_params: dict[str, Any] | None = None

        def get(self, url: str, *, params: dict[str, Any], timeout: float) -> Any:
            if self.raises is not None:
                raise self.raises
            self.last_params = params
            request = httpx.Request("GET", url)
            return httpx.Response(self.status_code, json=self.payload, request=request)

    stub = Registry()
    monkeypatch.setattr(nppes.httpx, "get", stub.get)
    return stub


class TestAWellFormedNumber:
    @pytest.mark.parametrize("npi", ["1999999984", " 1999999984 "])
    def test_ten_digits_is_well_formed(self, npi: str) -> None:
        assert nppes.is_well_formed(npi)

    @pytest.mark.parametrize("npi", ["", "123", "19999999841", "199999998X", "abcdefghij"])
    def test_anything_else_is_not(self, npi: str) -> None:
        assert not nppes.is_well_formed(npi)

    def test_a_malformed_number_is_never_sent_to_the_registry(self, registry: Any) -> None:
        # A typo costs nothing and the registry is not asked to validate our
        # input for us.
        assert nppes.look_up("123") is None
        assert registry.last_params is None


class TestWhatComesBack:
    def test_her_name_credential_and_taxonomy(self, registry: Any) -> None:
        provider = nppes.look_up("1999999984")

        assert provider is not None
        assert provider.legal_name == "TEST THERAPIST"
        assert provider.credential == "LCSW"
        assert provider.taxonomy_code == "101YM0800X"
        assert provider.taxonomy_description == "Mental Health Counselor"

    def test_the_practice_location_not_the_mailing_address(self, registry: Any) -> None:
        registry.payload = _payload(
            addresses=[
                {
                    "address_purpose": "MAILING",
                    "address_1": "PO BOX 900",
                    "city": "RALEIGH",
                    "state": "NC",
                },
                {
                    "address_purpose": "LOCATION",
                    "address_1": "14 MILL STREET",
                    "city": "DURHAM",
                    "state": "NC",
                },
            ]
        )

        provider = nppes.look_up("1999999984")

        assert provider is not None
        assert provider.address_line1 == "14 MILL STREET"
        assert provider.city == "DURHAM"

    def test_the_primary_taxonomy_when_there_are_several(self, registry: Any) -> None:
        registry.payload = _payload(
            taxonomies=[
                {"code": "999999999X", "desc": "Something else", "primary": False},
                {"code": "101YM0800X", "desc": "Mental Health Counselor", "primary": True},
            ]
        )

        provider = nppes.look_up("1999999984")

        assert provider is not None
        assert provider.taxonomy_code == "101YM0800X"

    def test_an_organisation_record_reads_its_organisation_name(self, registry: Any) -> None:
        registry.payload = _payload(basic={"organization_name": "TEST THERAPY PLLC"})

        provider = nppes.look_up("1999999984")

        assert provider is not None
        assert provider.legal_name == "TEST THERAPY PLLC"

    def test_the_npi_asked_for_is_the_npi_returned(self, registry: Any) -> None:
        provider = nppes.look_up("1999999984")

        assert provider is not None
        assert provider.npi == "1999999984"
        assert registry.last_params == {"version": "2.1", "number": "1999999984"}


class TestPartialRecordsAreOrdinary:
    def test_a_record_with_no_address(self, registry: Any) -> None:
        registry.payload = _payload(addresses=[])

        provider = nppes.look_up("1999999984")

        assert provider is not None
        assert provider.address_line1 is None
        assert provider.legal_name == "TEST THERAPIST"

    def test_a_record_with_no_taxonomy(self, registry: Any) -> None:
        registry.payload = _payload(taxonomies=[])

        provider = nppes.look_up("1999999984")

        assert provider is not None
        assert provider.taxonomy_code is None

    def test_a_blank_field_is_nothing_on_file_rather_than_a_blank(self, registry: Any) -> None:
        registry.payload = _payload(
            basic={"first_name": "TEST", "last_name": "", "credential": " "}
        )

        provider = nppes.look_up("1999999984")

        assert provider is not None
        assert provider.legal_name == "TEST"
        assert provider.credential is None


class TestNotFoundIsAFindingNotAFailure:
    def test_an_empty_result_set(self, registry: Any) -> None:
        registry.payload = {"result_count": 0, "results": []}

        assert nppes.look_up("1999999984") is None

    def test_a_missing_results_key(self, registry: Any) -> None:
        registry.payload = {"result_count": 0}

        assert nppes.look_up("1999999984") is None

    def test_the_errors_key_nppes_returns_with_a_200(self, registry: Any) -> None:
        # The registry reports a bad request in the body, not the status code.
        # Reading the status alone would turn "no such NPI" into a success
        # carrying no record, and the screen would say the wrong thing.
        registry.payload = {"Errors": [{"description": "Number is not valid"}]}

        assert nppes.look_up("1999999984") is None


class TestUnavailableIsDifferentFromNotFound:
    def test_a_transport_failure_raises(self, registry: Any) -> None:
        registry.raises = httpx.ConnectError("no route to host")

        with pytest.raises(nppes.NppesUnavailableError):
            nppes.look_up("1999999984")

    def test_a_timeout_raises(self, registry: Any) -> None:
        registry.raises = httpx.ReadTimeout("too slow")

        with pytest.raises(nppes.NppesUnavailableError):
            nppes.look_up("1999999984")

    def test_a_server_error_raises(self, registry: Any) -> None:
        registry.status_code = 500

        with pytest.raises(nppes.NppesUnavailableError):
            nppes.look_up("1999999984")

    def test_a_body_that_is_not_a_result_set_raises(self, registry: Any) -> None:
        registry.payload = ["not", "a", "dict"]

        with pytest.raises(nppes.NppesUnavailableError):
            nppes.look_up("1999999984")


def test_the_default_registry_is_the_public_one() -> None:
    # Named rather than assembled, so a typo in the host is a visible diff
    # rather than a lookup that quietly never finds anybody.
    assert nppes.DEFAULT_BASE_URL == "https://npiregistry.cms.hhs.gov/api/"


def test_the_timeout_is_short_enough_to_fail_in_front_of_her() -> None:
    # This runs inside a request she is waiting on. A slow registry must not
    # hold her setup open; the screen can always ask her to type it.
    assert nppes.TIMEOUT_SECONDS <= 10


class TestFactsTheRegistryCarriesThatWeAlmostMissed:
    """Fields that were on every record all along and were not being read."""

    def test_the_licence_recorded_against_the_primary_taxonomy(self, registry: Any) -> None:
        # Self-reported and never board-checked, which is why it is presented
        # for confirmation rather than treated as verification. But it is real
        # data, and asking her to retype what the registry already holds is
        # exactly what this screen exists to stop.
        registry.payload = _payload(
            taxonomies=[
                {
                    "code": "106H00000X",
                    "desc": "Marriage & Family Therapist",
                    "license": "MFT001741",
                    "state": "GA",
                    "primary": True,
                }
            ]
        )

        provider = nppes.look_up("1999999984")

        assert provider is not None
        assert provider.license_number == "MFT001741"
        assert provider.license_state == "GA"

    def test_a_record_with_no_licence_is_ordinary(self, registry: Any) -> None:
        provider = nppes.look_up("1999999984")

        assert provider is not None
        assert provider.license_number is None

    def test_an_active_record(self, registry: Any) -> None:
        registry.payload = _payload(basic={"first_name": "TEST", "status": "A"})

        provider = nppes.look_up("1999999984")

        assert provider is not None
        assert provider.active

    def test_a_deactivated_npi_is_flagged_rather_than_presented_as_hers(
        self, registry: Any
    ) -> None:
        # A deactivated number still answers a lookup. Presenting it as a clean
        # "here you are" would have her confirm a retired identifier.
        registry.payload = _payload(basic={"first_name": "TEST", "status": "D"})

        provider = nppes.look_up("1999999984")

        assert provider is not None
        assert not provider.active

    def test_a_missing_status_is_treated_as_active(self, registry: Any) -> None:
        # Not on every record. Refusing to show a record because a field was
        # absent is a worse answer than showing it.
        provider = nppes.look_up("1999999984")

        assert provider is not None
        assert provider.active

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("NPI-1", 1), ("NPI-2", 2), ("", None), (None, None), ("garbage", None)],
    )
    def test_individual_versus_organisation(
        self, registry: Any, raw: Any, expected: int | None
    ) -> None:
        # Tier 0 asks for an individual NPI and a billing NPI next to each
        # other, so pasting the practice's type-2 number into the individual
        # field is a mistake people will make. Catching it by name beats
        # confirming the wrong number and hearing about it from a payer.
        registry.payload = _payload(enumeration_type=raw)

        provider = nppes.look_up("1999999984")

        assert provider is not None
        assert provider.entity_type == expected


class TestSearchingByName:
    """For the clinician who cannot recall ten digits."""

    def _people(self, *names: tuple[str, str, str]) -> dict[str, Any]:
        return {
            "result_count": len(names),
            "results": [
                {
                    "number": npi,
                    "enumeration_type": "NPI-1",
                    "basic": {"first_name": first, "last_name": "THERAPIST", "status": "A"},
                    "taxonomies": [{"code": "101YM0800X", "desc": desc, "primary": True}],
                    "addresses": [{"address_purpose": "LOCATION", "city": "DURHAM", "state": "NC"}],
                }
                for npi, first, desc in names
            ],
        }

    def test_a_surname_is_required(self, registry: Any) -> None:
        assert nppes.search(last_name="   ") == []
        assert registry.last_params is None

    def test_the_state_is_sent_upper_cased(self, registry: Any) -> None:
        registry.payload = self._people(("1999999984", "TEST", "Counselor, Mental Health"))

        nppes.search(last_name="Therapist", state=" nc ")

        assert registry.last_params is not None
        assert registry.last_params["state"] == "NC"

    def test_no_taxonomy_filter_is_sent(self, registry: Any) -> None:
        # The load-bearing decision. Mental health is spread across Psych*,
        # Social Worker*, Counselor* and Marriage* in this registry, so a
        # psych-only filter would hide most LCSWs, LPCs and LMFTs - which is
        # most therapists. The taxonomy is shown on the row instead.
        registry.payload = self._people(("1999999984", "TEST", "Counselor, Mental Health"))

        nppes.search(last_name="Therapist", state="NC")

        assert registry.last_params is not None
        assert "taxonomy_description" not in registry.last_params

    def test_each_match_carries_what_identifies_it(self, registry: Any) -> None:
        registry.payload = self._people(
            ("1999999984", "TEST", "Counselor, Mental Health"),
            ("1841151289", "OTHER", "Marriage & Family Therapist"),
        )

        matches = nppes.search(last_name="Therapist", state="NC")

        assert [m.npi for m in matches] == ["1999999984", "1841151289"]
        assert matches[1].taxonomy_description == "Marriage & Family Therapist"
        assert matches[0].city == "DURHAM"

    def test_the_npi_comes_from_the_record_not_the_caller(self, registry: Any) -> None:
        # A search does not know the number in advance, and a row she cannot
        # identify is one she could never pick.
        registry.payload = self._people(("1841151289", "TEST", "Psychologist"))

        matches = nppes.search(last_name="Therapist")

        assert matches[0].npi == "1841151289"

    def test_a_record_with_no_number_is_skipped_rather_than_shown(self, registry: Any) -> None:
        registry.payload = {"result_count": 1, "results": [{"basic": {"first_name": "TEST"}}]}

        assert nppes.search(last_name="Therapist") == []

    def test_no_matches_is_an_empty_list_not_an_error(self, registry: Any) -> None:
        registry.payload = {"result_count": 0, "results": []}

        assert nppes.search(last_name="Nobody") == []

    def test_an_outage_still_raises(self, registry: Any) -> None:
        registry.raises = httpx.ConnectError("no route to host")

        with pytest.raises(nppes.NppesUnavailableError):
            nppes.search(last_name="Therapist")
