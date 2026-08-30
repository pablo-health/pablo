# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What kind of principal performed an audited action.

``audit_logs.user_id`` is the actor identifier as recorded, and it has no
foreign key on purpose so that system actions and unauthenticated probes are
captured rather than rejected. That was unambiguous while every actor was a
clinician. Once a second kind of principal can act it is not, because both
identifiers are uuids — and once an anonymous one can, ``user_id`` stops
naming the actor at all.

The tests below are mostly about the DEFAULT, because the default is what stops
this being a breaking change: every row written before the column existed, and
every caller that never learns about it, must keep the meaning it already had.
"""

from __future__ import annotations

from app.models.audit import (
    ACTOR_TYPE_ANONYMOUS,
    ACTOR_TYPE_CLINICIAN,
    ACTOR_TYPE_PATIENT,
    ACTOR_TYPE_PLATFORM_STAFF,
    ACTOR_TYPE_SYSTEM,
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

    def test_an_anonymous_actor_is_stated_explicitly(self) -> None:
        entry = AuditLogEntry(
            user_id="usr_1", action="patient_created", actor_type=ACTOR_TYPE_ANONYMOUS
        )
        assert entry.actor_type == ACTOR_TYPE_ANONYMOUS


class TestAnonymous:
    def test_user_id_names_the_scope_not_the_actor(self) -> None:
        """The row an anonymous public booking writes.

        There is no identifier for the booker to put in ``user_id``, and the
        row still has to be written under the owner's RLS context and show up
        in the owner's own trail. So ``user_id`` keeps naming the clinician —
        the SCOPE the write happened inside — and ``actor_type`` is what stops
        the row reading as though that clinician created the chart.
        """
        owner_id = "0198f3a1-0000-7000-8000-000000000002"
        row = AuditLogEntry(
            user_id=owner_id,
            action="patient_created",
            actor_type=ACTOR_TYPE_ANONYMOUS,
            ip_address="203.0.113.9",
            changes={"source": "public_booking"},
        )

        assert row.user_id == owner_id
        assert row.actor_type != ACTOR_TYPE_CLINICIAN
        # Who acted is answered by the request context plus provenance, since
        # the principal has no id of its own.
        assert row.ip_address == "203.0.113.9"
        assert row.changes == {"source": "public_booking"}

    def test_the_kind_survives_a_dict_round_trip(self) -> None:
        # to_dict/from_dict is the shape audit rows travel in; dropping the
        # discriminator there would silently re-attribute the row to a
        # clinician, which is the bug this kind exists to fix.
        row = AuditLogEntry(user_id="usr_1", action="patient_created")
        for kind in ACTOR_TYPES:
            row.actor_type = kind
            assert AuditLogEntry.from_dict(row.to_dict()).actor_type == kind


class TestVocabulary:
    def test_the_five_kinds_are_the_whole_vocabulary(self) -> None:
        assert ACTOR_TYPES == (
            ACTOR_TYPE_CLINICIAN,
            ACTOR_TYPE_PATIENT,
            ACTOR_TYPE_ANONYMOUS,
            ACTOR_TYPE_SYSTEM,
            ACTOR_TYPE_PLATFORM_STAFF,
        )

    def test_the_values_are_the_stored_strings(self) -> None:
        # The migration writes these literals into a server default; a rename
        # here without a migration would silently orphan every existing row.
        assert ACTOR_TYPE_CLINICIAN == "clinician"
        assert ACTOR_TYPE_PATIENT == "patient"
        assert ACTOR_TYPE_ANONYMOUS == "anonymous"
        assert ACTOR_TYPE_SYSTEM == "system"
        assert ACTOR_TYPE_PLATFORM_STAFF == "platform_staff"


class TestNonHumanActors:
    """The two kinds that stop the log claiming a practitioner acted."""

    def test_automated_work_is_not_recorded_as_a_clinician(self) -> None:
        # A background job reading a chart to compose an email is not the
        # therapist opening that chart, and the six-year record has to be
        # able to tell a reader which one happened.
        entry = AuditLogEntry(
            user_id="0198f3a1-0000-7000-8000-000000000001",
            action="patient_viewed",
            actor_type=ACTOR_TYPE_SYSTEM,
            actor_component="inbox.draft_worker",
        )
        assert entry.actor_type != ACTOR_TYPE_CLINICIAN
        assert entry.actor_component == "inbox.draft_worker"

    def test_a_system_row_scopes_to_the_practice_rather_than_naming_an_actor(self) -> None:
        # Same shape as anonymous: user_id is the principal whose data was
        # touched, not somebody who clicked anything.
        scope = "0198f3a1-0000-7000-8000-000000000002"
        job = AuditLogEntry(user_id=scope, action="patient_viewed", actor_type=ACTOR_TYPE_SYSTEM)
        human = AuditLogEntry(user_id=scope, action="patient_viewed")
        assert job.user_id == human.user_id
        assert job.actor_type != human.actor_type

    def test_operator_access_is_its_own_kind(self) -> None:
        # Someone operating the deployment reading a practice's data is
        # neither the practice's clinician nor an unattended job; a human is
        # individually accountable, so user_id really is the actor here.
        entry = AuditLogEntry(
            user_id="0198f3a1-0000-7000-8000-000000000003",
            action="patient_viewed",
            actor_type=ACTOR_TYPE_PLATFORM_STAFF,
        )
        assert entry.actor_type not in (ACTOR_TYPE_CLINICIAN, ACTOR_TYPE_SYSTEM)


class TestActorComponent:
    def test_it_defaults_to_absent_for_every_human_kind(self) -> None:
        # Naming a component for a human actor would be noise: user_id
        # already says who acted.
        for kind in (ACTOR_TYPE_CLINICIAN, ACTOR_TYPE_PATIENT, ACTOR_TYPE_PLATFORM_STAFF):
            assert AuditLogEntry(action="patient_viewed", actor_type=kind).actor_component is None

    def test_an_entry_that_never_mentions_it_is_unchanged(self) -> None:
        # The whole column is additive; a caller that never learns about it
        # must keep writing exactly the row it wrote before.
        assert AuditLogEntry(action="patient_viewed").actor_component is None


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
