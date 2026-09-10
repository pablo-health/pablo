# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Turning an 837P-shaped claim into the vendor's native submission.

The request built in ``app.claims.wire`` speaks X12: ``M`` for male,
``20260901`` for a date, and lines that point at diagnoses by position. The
vendor's own claim API takes named values, ISO dates, and codes. These tests
cover the translation, with attention to the places where getting it wrong
would file a claim that is accepted and *wrong* rather than rejected — a
different sex, a different date of service, a diagnosis attached to the wrong
line.

The recorded 837P request the whole suite starts from is a real one, accepted
by the vendor's test payer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from app.claims.clearinghouse import (
    ClearinghouseRequestChangedError,
    ClearinghouseValidationError,
)
from app.claims.sdk_submission import result_from_sdk, submission_error, to_sdk_submission
from app.models.claims_transport import ClaimSubmissionRequest
from stedi.models import (
    ClaimRejectionError,
    CreateProfessionalClaimSubmissionOutput,
    InvalidRequestException,
    ValidationFailure,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "clearinghouse"
_KEY = "claim-0001:1:attempt-1"


def _request(name: str = "837p_request_test_payer.json") -> ClaimSubmissionRequest:
    return ClaimSubmissionRequest.model_validate(json.loads((_FIXTURES / name).read_text()))


def _submission(**overrides: Any) -> Any:
    """The recorded claim as a native submission, with the request tweaked."""
    request = _request()
    if overrides:
        request = request.model_copy(update=overrides)
    return to_sdk_submission(request, idempotency_key=_KEY)


class TestThePartiesSurviveTheTranslation:
    def test_the_payer_is_identified_by_id_alone(self) -> None:
        """Not by the receiver's name.

        The 837P names the clearinghouse as its receiver, never the payer, so
        a translation that reached for a name here would file every claim in
        the system against a payer called "Stedi".
        """
        submission = _submission()

        assert submission.payer.id == "STEDI"
        assert submission.payer.name is None

    def test_the_policy_holder_becomes_the_insured(self) -> None:
        submission = _submission()

        assert submission.insured.member_id == "123456789"
        assert submission.insured.name.value.first_name == "John"
        assert submission.insured.name.value.last_name == "Anon"
        assert submission.insured.policy_or_group_number == "3335555"

    def test_the_practices_tax_id_and_npi_reach_the_billing_block(self) -> None:
        submission = _submission()

        assert submission.billing.tax_id.value == "844459714"
        assert submission.billing.billing_provider.identifiers.npi == "1999999984"
        assert submission.billing.patient_control_number == "88659891"
        assert submission.billing.total_charge == "150.00"

    def test_a_claim_for_the_policy_holder_carries_no_patient_block(self) -> None:
        """The 837P leaves out the dependent loop when the client holds the
        policy, and the vendor's shape says the same thing by omission."""
        assert _submission().patient is None


class TestValuesThatWouldBeWrongRatherThanRejected:
    """The dangerous half of a translation.

    A mistranslated enum or date does not fail — it files a claim that says
    something false about a person, and the payer adjudicates it.
    """

    @pytest.mark.parametrize(("letter", "word"), [("M", "MALE"), ("F", "FEMALE"), ("U", "UNKNOWN")])
    def test_every_sex_letter_has_a_word(self, letter: str, word: str) -> None:
        request = _request()
        subscriber = request.subscriber.model_copy(update={"gender": letter})
        submission = to_sdk_submission(
            request.model_copy(update={"subscriber": subscriber}), idempotency_key=_KEY
        )

        assert submission.insured.gender.value == word

    def test_dates_of_birth_become_iso(self) -> None:
        assert _submission().insured.date_of_birth == "2000-01-01"

    def test_a_service_date_stays_a_single_day(self) -> None:
        """One session happens on one day.

        The vendor takes a range here, and filling both ends in is not the
        same statement: it becomes a range segment in X12 rather than a
        service date, and the payer's remittance echoes it back differently.
        """
        line = _submission().service_lines[0]

        assert line.dates_of_service.start == "2026-09-01"
        assert line.dates_of_service.end is None

    def test_the_procedure_keeps_its_modifiers(self) -> None:
        line = _submission().service_lines[0]

        assert line.procedure_code.code == "90837"
        assert line.procedure_code.modifiers == ["95"]

    def test_the_charge_and_units_are_unchanged(self) -> None:
        line = _submission().service_lines[0]

        assert line.line_item_charge_amount == "150.00"
        assert line.units == "1"


class TestDiagnosisPointersBecomeDiagnoses:
    """The one place the native shape removes a class of error outright.

    An 837P line points at a diagnosis by position, so a pointer off the end
    of the list is a claim the payer rejects. Resolving the pointer here means
    the line carries the code itself and cannot point at nothing.
    """

    def test_the_claims_first_diagnosis_is_the_primary(self) -> None:
        assert _submission().encounter.primary_diagnosis_code == "F411"

    def test_a_single_diagnosis_leaves_no_additional_list(self) -> None:
        assert _submission().encounter.additional_diagnosis_codes is None

    def test_a_line_carries_the_code_its_pointer_named(self) -> None:
        assert _submission().service_lines[0].diagnosis_codes == ["F411"]

    def test_a_second_diagnosis_is_reachable_by_its_pointer(self) -> None:
        request = _request()
        information = request.claimInformation
        diagnoses = [
            *information.healthCareCodeInformation,
            information.healthCareCodeInformation[0].model_copy(
                update={"diagnosisCode": "F332", "diagnosisTypeCode": "ABF"}
            ),
        ]
        line = information.serviceLines[0]
        service = line.professionalService.model_copy(
            update={
                "compositeDiagnosisCodePointers": (
                    line.professionalService.compositeDiagnosisCodePointers.model_copy(
                        update={"diagnosisCodePointers": ["2"]}
                    )
                )
            }
        )
        request = request.model_copy(
            update={
                "claimInformation": information.model_copy(
                    update={
                        "healthCareCodeInformation": diagnoses,
                        "serviceLines": [line.model_copy(update={"professionalService": service})],
                    }
                )
            }
        )

        submission = to_sdk_submission(request, idempotency_key=_KEY)

        assert submission.encounter.additional_diagnosis_codes == ["F332"]
        assert submission.service_lines[0].diagnosis_codes == ["F332"]

    def test_a_pointer_past_the_end_is_dropped_rather_than_crashing(self) -> None:
        """Left for the vendor's edits to report.

        A line with no diagnosis is a rejection the biller can read and act
        on; an exception three layers below the claim stalls the whole
        submission pass with a traceback instead.
        """
        request = _request()
        information = request.claimInformation
        line = information.serviceLines[0]
        service = line.professionalService.model_copy(
            update={
                "compositeDiagnosisCodePointers": (
                    line.professionalService.compositeDiagnosisCodePointers.model_copy(
                        update={"diagnosisCodePointers": ["1", "4"]}
                    )
                )
            }
        )
        request = request.model_copy(
            update={
                "claimInformation": information.model_copy(
                    update={
                        "serviceLines": [line.model_copy(update={"professionalService": service})]
                    }
                )
            }
        )

        submission = to_sdk_submission(request, idempotency_key=_KEY)

        assert submission.service_lines[0].diagnosis_codes == ["F411"]


class TestResubmissionsSayWhatTheyReplace:
    def test_an_original_claim_carries_no_resubmission_block(self) -> None:
        assert _submission().encounter.resubmission is None

    @pytest.mark.parametrize(
        ("frequency", "code"),
        [("7", "REPLACEMENT_OF_PRIOR_CLAIM"), ("8", "CANCELLATION_OF_PRIOR_CLAIM")],
    )
    def test_a_correction_names_what_it_replaces(self, frequency: str, code: str) -> None:
        request = _request()
        information = request.claimInformation.model_copy(update={"claimFrequencyCode": frequency})
        submission = to_sdk_submission(
            request.model_copy(update={"claimInformation": information}), idempotency_key=_KEY
        )

        assert submission.encounter.resubmission is not None
        assert submission.encounter.resubmission.code.value == code


class TestTheKeyThatMakesResendingSafe:
    def test_it_travels_on_the_body(self) -> None:
        """The old endpoint took it as a header. Losing it in the move would
        turn every retry of a timed-out submission into a duplicate claim."""
        assert _submission().idempotency_key == _KEY


class TestReadingTheVendorsAnswer:
    def test_an_accepted_claim_stores_the_claim_id_not_the_submission_id(self) -> None:
        """The id that survives a resubmission.

        The vendor mints a new submission id every attempt and keeps one
        claim id for the claim's life, and it is the claim id its
        acknowledgements and remittances are matched against. Storing the
        submission id would leave a corrected claim unable to find its own
        payments.
        """
        output = CreateProfessionalClaimSubmissionOutput(
            claim_id="clm_01ABC", submission_id="sbm_01XYZ"
        )

        result = result_from_sdk(output, req=_request())

        assert result.status == "SUCCESS"
        assert result.claimReference is not None
        assert result.claimReference.correlationId == "clm_01ABC"
        assert result.meta.traceId == "sbm_01XYZ"

    def test_an_edit_rejection_is_an_answer_rather_than_a_failure(self) -> None:
        """The vendor stored the claim and wrote a rejection against it; it
        just did not send it to the payer. That is a result the worker
        rejects the claim on, not an exception."""
        output = CreateProfessionalClaimSubmissionOutput(
            claim_id="clm_01ABC",
            submission_id="sbm_01XYZ",
            errors=[ClaimRejectionError(description="Diagnosis code pointer is invalid")],
        )

        result = result_from_sdk(output, req=_request())

        assert result.status == "ERROR"
        assert result.claimReference is None
        assert [error.description for error in result.errors] == [
            "Diagnosis code pointer is invalid"
        ]

    def test_the_control_number_is_echoed_from_the_claim_we_sent(self) -> None:
        output = CreateProfessionalClaimSubmissionOutput(
            claim_id="clm_01ABC", submission_id="sbm_01XYZ"
        )

        result = result_from_sdk(output, req=_request())

        assert result.controlNumber == "88659891"
        assert result.tradingPartnerServiceId == "STEDI"


class TestTellingTwoRefusalsApart:
    """Both arrive as the same exception type, and mean opposite things.

    One says the claim is wrong; the other says the claim is fine and the key
    is stale. A biller sent to fix a correct claim will not find anything
    wrong with it.
    """

    def test_a_reused_key_reads_as_a_reused_key(self) -> None:
        exc = InvalidRequestException(
            "This Idempotency-Key was previously used with a different request."
        )

        assert isinstance(submission_error(exc), ClearinghouseRequestChangedError)

    def test_a_malformed_claim_still_reads_as_a_malformed_claim(self) -> None:
        exc = InvalidRequestException(
            "Request body failed schema validation",
            errors=[
                ValidationFailure(
                    message='"authorization" is a required property', path="/authorization"
                )
            ],
        )

        assert isinstance(submission_error(exc), ClearinghouseValidationError)
