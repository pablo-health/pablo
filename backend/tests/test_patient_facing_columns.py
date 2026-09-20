# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A new column on ``patients`` or ``appointments`` must be decided, not default.

Both tables are readable in full by their own patient at the database layer.
Row-level security grants the row; it has no column granularity, and no policy
can be written that would give it any. So nothing underneath will notice a
column arriving and quietly reaching a patient-facing response — which is what
this file is for.

It asserts the three things that have to agree, for each table:

1. every ORM column carries a decision in ``app.models.patient_facing``,
2. the patient-facing response model carries exactly the shown ones,
3. the repository's projection selects exactly the shown ones.

Adding a column to either ORM model fails (1) until somebody writes down what
happens to it. Widening the model or the query without recording the decision
fails (2) or (3). What comes out on the wire is asserted separately, on real
serialised responses, in ``test_patient_appointments_routes.py`` and
``test_patient_intake_api.py`` — a serializer that leaks by default would
satisfy everything here.
"""

from __future__ import annotations

import pytest
from app.db.models import AppointmentRow, PatientRow
from app.models.patient_facing import (
    APPOINTMENT_COLUMN_DECISIONS,
    PATIENT_COLUMN_DECISIONS,
    PATIENT_CONTACT_CHANNEL_COLUMNS,
    PATIENT_SELF_WRITABLE_COLUMNS,
    PatientAppointmentResponse,
    PatientFacingPatient,
    shown_columns,
    withheld_columns,
)
from app.repositories.postgres.appointment import (
    PATIENT_FACING_COLUMNS as APPOINTMENT_PROJECTION,
)
from app.repositories.postgres.patient import (
    PATIENT_FACING_COLUMNS as PATIENT_PROJECTION,
)

# (table name, ORM model, decisions, response model, repository projection)
_TABLES = [
    pytest.param(
        "patients",
        PatientRow,
        PATIENT_COLUMN_DECISIONS,
        PatientFacingPatient,
        PATIENT_PROJECTION,
        id="patients",
    ),
    pytest.param(
        "appointments",
        AppointmentRow,
        APPOINTMENT_COLUMN_DECISIONS,
        PatientAppointmentResponse,
        APPOINTMENT_PROJECTION,
        id="appointments",
    ),
]


@pytest.mark.parametrize(("table", "orm", "decisions", "model", "projection"), _TABLES)
class TestEveryColumnIsDecided:
    def test_no_column_is_missing_a_decision(self, table, orm, decisions, model, projection):
        """The check that catches a column added tomorrow.

        A new column is not in the registry, so this fails and the person
        adding it writes down whether a patient may see it. That is the whole
        mechanism: the decision becomes a diff somebody reviews, instead of a
        default nobody chose.
        """
        undecided = set(orm.__table__.columns.keys()) - set(decisions)
        assert not undecided, (
            f"{table}.{sorted(undecided)} reached the ORM without a patient-facing "
            f"decision — add it to app.models.patient_facing, shown or withheld"
        )

    def test_no_decision_names_a_column_that_is_gone(
        self, table, orm, decisions, model, projection
    ):
        """The registry is about this table, not a remembered version of it."""
        stale = set(decisions) - set(orm.__table__.columns.keys())
        assert not stale, f"{table} has no column {sorted(stale)}"

    def test_every_withheld_column_gives_a_reason(self, table, orm, decisions, model, projection):
        """One line each, so the choice is reviewable rather than accidental."""
        for column, reason in withheld_columns(decisions).items():
            assert reason.strip(), f"{table}.{column} is withheld with no reason"
            assert "\n" not in reason, f"{table}.{column}'s reason is not one line"


@pytest.mark.parametrize(("table", "orm", "decisions", "model", "projection"), _TABLES)
class TestTheModelAndTheQueryAgreeWithTheDecisions:
    def test_the_response_model_carries_exactly_the_shown_columns(
        self, table, orm, decisions, model, projection
    ):
        assert set(model.model_fields) == shown_columns(decisions)

    def test_the_repository_selects_exactly_the_shown_columns(
        self, table, orm, decisions, model, projection
    ):
        """The read is the first half of the control and the narrower one.

        A withheld column the query never selects cannot be serialised by
        accident, cannot be logged by accident, and is not in memory for a
        later refactor to reach for.
        """
        assert {column.key for column in projection} == shown_columns(decisions)


class TestTheDecisionsCoverWhatTheBeadWasFiledAbout:
    """Named columns, asserted by name.

    Derived checks pass when both sides of a derivation move together, which is
    exactly what a mistake here would look like. These are the columns that
    motivated the control: a diff that shows one of them becoming visible is
    the diff to stop.
    """

    @pytest.mark.parametrize(
        "column",
        [
            "diagnosis",
            "sliding_scale_note",
            "rate_cents",
            "chart_closure_reason",
            "origin",
            "deleted_at",
            "chart_closed_at",
        ],
    )
    def test_the_staff_authored_chart_columns_are_withheld(self, column: str) -> None:
        assert column in withheld_columns(PATIENT_COLUMN_DECISIONS)
        assert column not in PatientFacingPatient.model_fields

    @pytest.mark.parametrize(
        "column",
        [
            "diagnosis_codes",
            "service_code",
            "modifiers",
            "unit_count",
            "place_of_service",
            "notes",
            "user_id",
            "session_id",
            "confirmation_token_hash",
            "ehr_appointment_url",
            "google_event_id",
            "google_calendar_id",
            "google_sync_status",
            "ical_uid",
            "ical_source",
            "ical_sync_status",
        ],
    )
    def test_the_staff_authored_appointment_columns_are_withheld(self, column: str) -> None:
        assert column in withheld_columns(APPOINTMENT_COLUMN_DECISIONS)
        assert column not in PatientAppointmentResponse.model_fields

    def test_the_appointment_allow_list_is_the_one_that_was_agreed(self) -> None:
        """Times, status, type and the link — plus what a patient did themselves.

        ``late_cancellation`` is the one addition to the agreed allow-list, and
        it is deliberate: being charged a late-cancellation fee without ever
        being told the cancellation counted as late is the surprise the rest of
        this file exists to prevent. ``id``, ``duration_minutes`` and the
        recurrence pair are what the portal needs to render and cancel an
        appointment; none is staff-authored.

        ``provider`` is the second addition, and it is about the same screen as
        ``video_link``: it says the appointment is held on a video service
        before the link is worth offering, which is what lets the portal say a
        link is coming instead of showing an empty row. It names a product,
        not a person, and the practice chose it.
        """
        assert shown_columns(APPOINTMENT_COLUMN_DECISIONS) == {
            "id",
            "start_at",
            "end_at",
            "duration_minutes",
            "status",
            "session_type",
            "video_link",
            "video_platform",
            "provider",
            "recurrence_rule",
            "recurring_appointment_id",
            "late_cancellation",
        }

    def test_the_chart_allow_list_is_identity_and_the_patients_own_contact_details(
        self,
    ) -> None:
        """Who the chart says they are, and how to reach them. Nothing else.

        The contact and address columns joined the identity three when the
        portal profile screen landed: a screen that asks somebody to check
        their address has to show them the address, and the person is the
        one who knows when it changed.

        What stayed withheld is the whole point of the list and is asserted
        column by column above — the working diagnosis, the note about what
        they can afford, the rate, the chart-closure reason, the consent
        bookkeeping. Every one of those is staff-authored and none of it is
        news a portal should break.

        ``sex`` stays out too, which is easy to mistake for an oversight: it
        is the X12 administrative code an insurance claim carries, not a
        fact about the person, and a patient editing it would be editing a
        billing field.
        """
        assert shown_columns(PATIENT_COLUMN_DECISIONS) == {
            "first_name",
            "last_name",
            "preferred_name",
            "date_of_birth",
            "email",
            "phone",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
        }

    def test_only_contact_details_and_a_preferred_name_are_writable(self) -> None:
        """The write allow-list is narrower than the read one, and stays so.

        Identity is shown and not writable: a patient whose legal name or
        date of birth is wrong is telling the practice something a person
        has to look at, and the intake form already collects exactly that
        correction. If this set grows, it should be a deliberate edit here.
        """
        assert {
            "preferred_name",
            "email",
            "phone",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
        } == PATIENT_SELF_WRITABLE_COLUMNS

    def test_every_writable_column_is_also_shown(self) -> None:
        """A field a patient may change is a field they must be able to see.

        A form that writes a value it never displayed is one the person
        cannot check before submitting it.
        """
        assert shown_columns(PATIENT_COLUMN_DECISIONS) >= PATIENT_SELF_WRITABLE_COLUMNS

    def test_the_delivery_channels_are_a_subset_of_what_is_writable(self) -> None:
        """The two audited-separately fields are two of the writable ones.

        ``PATIENT_CONTACT_CHANNEL_COLUMNS`` exists to mark which writes move
        where a future credential is sent. A name in it that nobody can
        write would be a rule with nothing to apply to.
        """
        assert PATIENT_CONTACT_CHANNEL_COLUMNS <= PATIENT_SELF_WRITABLE_COLUMNS
