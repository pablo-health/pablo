# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What kind of principal performed an audited action.

``audit_logs.user_id`` is the actor identifier as recorded, and it has no
foreign key on purpose so that system actions and unauthenticated probes are
captured rather than rejected. That was unambiguous while every actor was a
clinician. Once a second kind of principal can act it is not, because both
identifiers are uuids.

The tests below are mostly about the DEFAULT, because the default is what stops
this being a breaking change: every row written before the column existed, and
every caller that never learns about it, must keep the meaning it already had.
"""

from __future__ import annotations

from app.models.audit import (
    ACTOR_TYPE_CLINICIAN,
    ACTOR_TYPE_PATIENT,
    ACTOR_TYPES,
    AuditLogEntry,
)


class TestDefault:
    def test_an_entry_that_never_mentions_actor_type_is_a_clinician(self) -> None:
        # Every existing caller. If this ever fails, the column stopped being
        # backwards compatible and a lot of history silently changed meaning.
        entry = AuditLogEntry(user_id="usr_1", action="patient_viewed")
        assert entry.actor_type == ACTOR_TYPE_CLINICIAN

    def test_a_patient_actor_is_stated_explicitly(self) -> None:
        entry = AuditLogEntry(
            user_id="pat_1", action="consent_changed", actor_type=ACTOR_TYPE_PATIENT
        )
        assert entry.actor_type == ACTOR_TYPE_PATIENT


class TestVocabulary:
    def test_the_two_kinds_are_the_whole_vocabulary(self) -> None:
        assert ACTOR_TYPES == (ACTOR_TYPE_CLINICIAN, ACTOR_TYPE_PATIENT)

    def test_the_values_are_the_stored_strings(self) -> None:
        # The migration writes these literals into a server default; a rename
        # here without a migration would silently orphan every existing row.
        assert ACTOR_TYPE_CLINICIAN == "clinician"
        assert ACTOR_TYPE_PATIENT == "patient"


class TestDisambiguation:
    def test_the_same_id_means_different_things_under_different_actor_types(self) -> None:
        """The reason the column exists.

        Both ids are uuids. Without the discriminator these two rows are
        indistinguishable, and one of them is a patient reading their own
        record while the other is a clinician reading somebody else's.
        """
        shared_id = "0198f3a1-0000-7000-8000-000000000001"
        clinician = AuditLogEntry(user_id=shared_id, action="patient_viewed")
        patient = AuditLogEntry(
            user_id=shared_id, action="patient_viewed", actor_type=ACTOR_TYPE_PATIENT
        )

        assert clinician.user_id == patient.user_id
        assert clinician.actor_type != patient.actor_type
