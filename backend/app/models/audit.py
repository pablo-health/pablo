# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Audit logging models for HIPAA compliance."""

import base64
import binascii
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from ..utcnow import utc_now_iso


class AuditAction(StrEnum):
    """Actions tracked in audit log for HIPAA compliance."""

    # Patient operations
    PATIENT_CREATED = "patient_created"
    PATIENT_LISTED = "patient_listed"
    PATIENT_VIEWED = "patient_viewed"
    PATIENT_UPDATED = "patient_updated"
    PATIENT_DELETED = "patient_deleted"
    PATIENT_RESTORED = "patient_restored"
    PATIENT_EXPORTED = "patient_exported"
    PATIENT_PURGED = "patient_purged"
    # Chart closure (THERAPY-hek). Orthogonal to soft-delete: closing a
    # chart marks the clinical/administrative care episode as ended, but
    # the row stays live and the day-30 hard-purge clock (THERAPY-cgy)
    # is not advanced.
    CHART_CLOSED = "chart_closed"
    CHART_REOPENED = "chart_reopened"

    # Session operations
    SESSION_CREATED = "session_created"
    SESSION_LISTED = "session_listed"
    SESSION_VIEWED = "session_viewed"
    SESSION_UPDATED = "session_updated"
    SESSION_FINALIZED = "session_finalized"
    SESSION_RATING_UPDATED = "session_rating_updated"
    SESSION_TRANSCRIPT_UPLOADED = "session_transcript_uploaded"
    SESSION_AUDIO_UPLOADED = "session_audio_uploaded"
    # The SOAP note (clinical PHI) is written by the off-request generation
    # worker, not on the upload request — so the note's creation is audited
    # there, at the point the PHI actually comes into existence.
    SESSION_NOTE_GENERATED = "session_note_generated"
    # Recorded session audio deleted by the per-practice audio retention
    # cron (THERAPY-ab7). The action value is defined here so audit-log
    # readers and dashboards render it consistently regardless of which
    # job entrypoint emitted the row.
    AUDIO_PURGED = "audio_purged"

    # iCal sync / EHR client import operations
    CLIENT_RESOLVED = "client_resolved"
    CLIENTS_IMPORTED = "clients_imported"
    # A sync run that surfaces unmatched external calendar events — each
    # carries a client_identifier (an external client name), so the read is
    # PHI-adjacent. The `changes` payload stays PHI-free (counts only); the
    # identifiers themselves are never recorded.
    ICAL_CALENDAR_SYNCED = "ical_calendar_synced"
    # A therapist attesting that the Google account they connected is
    # covered by an agreement their own practice holds, which is what
    # permits a patient's name to be written onto that calendar. Recorded
    # as evidence rather than as a preference: it says who attested, when,
    # and which calendar account it covered, and it outlives the
    # connection it was made about.
    CALENDAR_NAME_DISCLOSURE_ATTESTED = "calendar_name_disclosure_attested"

    # Appointment operations
    APPOINTMENT_CREATED = "appointment_created"
    APPOINTMENT_LISTED = "appointment_listed"
    APPOINTMENT_VIEWED = "appointment_viewed"
    APPOINTMENT_UPDATED = "appointment_updated"
    APPOINTMENT_CANCELLED = "appointment_cancelled"
    APPOINTMENT_SERIES_CREATED = "appointment_series_created"
    APPOINTMENT_SERIES_UPDATED = "appointment_series_updated"
    APPOINTMENT_SERIES_CANCELLED = "appointment_series_cancelled"

    # Admin operations
    EXPORT_QUEUE_VIEWED = "export_queue_viewed"
    EXPORT_ACTION_TAKEN = "export_action_taken"
    TENANT_EXPORTED = "tenant_exported"

    # Tenant management
    TENANT_LISTED = "tenant_listed"
    TENANT_VIEWED = "tenant_viewed"
    TENANT_DISABLED = "tenant_disabled"
    TENANT_ENABLED = "tenant_enabled"
    TENANT_DELETED = "tenant_deleted"
    # Offboarding scheduled but tenant is still active during the grace
    # window. Distinct from TENANT_DISABLED (which implies access cut)
    # and TENANT_DELETED (which implies the schema is gone).
    TENANT_OFFBOARD_SCHEDULED = "tenant_offboard_scheduled"

    # Tenant invitations — minted by platform admins to onboard a new
    # owner without a marketing-checkout round-trip. Lives in OSS so
    # a downstream deployment's overlay can log via the type-safe
    # AuditService API rather than persisting raw strings.
    INVITATION_CREATED = "invitation_created"
    INVITATION_REISSUED = "invitation_reissued"
    INVITATION_REVOKED = "invitation_revoked"
    INVITATION_ACCEPTED = "invitation_accepted"
    INVITATION_EMAIL_SWITCHED = "invitation_email_switched"

    # Practice/tenant configuration writes. Used for retention-policy
    # changes (e.g. per-practice audio retention slider — THERAPY-6k7)
    # and any future configurable retention surfaces. The `changes`
    # payload carries {field, previous, new} so audit readers can
    # reconstruct what was modified without storing PHI.
    RETENTION_UPDATED = "retention_updated"

    # EHR navigation
    EHR_NAVIGATE = "ehr_navigate"

    # User reading their own audit trail (meta-audit).
    SELF_AUDIT_VIEWED = "self_audit_viewed"

    # Per-user opt-in for chat quality review (THERAPY-8biz / opt-in
    # content capture). Recorded on every state change so the user can
    # see their own consent history. Payload carries no PHI — the
    # `changes` dict is the bare new state, e.g. ``{"opt_in": true}``.
    CHAT_QUALITY_REVIEW_OPT_IN = "chat_quality_review_opt_in"
    CHAT_QUALITY_REVIEW_OPT_OUT = "chat_quality_review_opt_out"
    CHAT_QUALITY_REVIEW_PURGE_REQUESTED = "chat_quality_review_purge_requested"

    # Per-user opt-in for quality review of session-derived notes
    # (session transcript + generated note text). Separate from the chat
    # consent above because session-derived content is a distinct surface;
    # recorded on every state change. Same no-PHI payload rule — `changes`
    # carries the bare new state, e.g. ``{"opt_in": true}``.
    SESSION_NOTES_QUALITY_REVIEW_OPT_IN = "session_notes_quality_review_opt_in"
    SESSION_NOTES_QUALITY_REVIEW_OPT_OUT = "session_notes_quality_review_opt_out"
    SESSION_NOTES_QUALITY_REVIEW_PURGE_REQUESTED = "session_notes_quality_review_purge_requested"

    # Per-user opt-in for quality review of email-reply drafting (the inbound
    # message plus the AI-drafted reply the clinician then edits). Separate from
    # chat and session-notes above because email correspondence is a distinct
    # surface a clinician may allow independently. Same no-PHI payload rule —
    # `changes` carries the bare new state, e.g. ``{"opt_in": true}``.
    INBOX_QUALITY_REVIEW_OPT_IN = "inbox_quality_review_opt_in"
    INBOX_QUALITY_REVIEW_OPT_OUT = "inbox_quality_review_opt_out"
    INBOX_QUALITY_REVIEW_PURGE_REQUESTED = "inbox_quality_review_purge_requested"

    # Onboarding milestones. Recorded regardless of whether PHI has been
    # touched — these are compliance events (BAA is a legal agreement, MFA
    # is a security control, security guide is a HIPAA § 164.308(a)(5)
    # training acknowledgment). Useful for detecting onboarding abandonment
    # (any user with onboarding_started but no baa_accepted row is stalled).
    ONBOARDING_STARTED = "onboarding_started"
    ONBOARDING_BAA_ACCEPTED = "onboarding_baa_accepted"
    ONBOARDING_MFA_ENROLLED = "onboarding_mfa_enrolled"
    ONBOARDING_SECURITY_GUIDE_ACKNOWLEDGED = "onboarding_security_guide_acknowledged"
    ONBOARDING_COMPLETED = "onboarding_completed"

    # The authorisation letting Pablo apply to insurance panels on a
    # clinician's behalf: sign her name to a payer's form, and ring the payer
    # to chase it. Not a HIPAA event — no PHI is involved — but the moment
    # Pablo acquires authority to act for someone with a third party, which is
    # exactly the kind of thing that has to be answerable for afterwards. The
    # revocation is recorded for the same reason and with the same weight: when
    # the authority ENDED is the half a complaint usually turns on.
    PAYER_AUTHORIZATION_SIGNED = "payer_authorization_signed"
    PAYER_AUTHORIZATION_REVOKED = "payer_authorization_revoked"

    # Account-recovery / authentication security events (HIPAA
    # § 164.308(a)(5)(ii)(C) login monitoring, § 164.312(b) audit controls).
    # A one-time backup code redeemed as the second factor is the highest-value
    # recovery path in the system — it mints an MFA-satisfied session, so the
    # event needs a durable, queryable record, not just an app log line.
    RECOVERY_CODE_REDEEMED = "recovery_code_redeemed"
    # A passwordless passkey assertion that minted a session. The primary
    # passwordless sign-in event, audited alongside recovery so login
    # monitoring covers the whole authenticator surface.
    PASSKEY_AUTHENTICATED = "passkey_authenticated"

    # Patient-context chat (THERAPY-bhv). Two-tier audit policy per
    # docs/architecture/patient-context-chat-oss.md §10: lifecycle events
    # land in the audit log; per-turn detail lives on chat_messages rows.
    CHAT_CONVERSATION_CREATED = "chat_conversation_created"
    # Read-access event for the conversation-detail endpoint, which returns
    # full message bodies. Mirrors PATIENT_VIEWED / SESSION_VIEWED so chat
    # reads are audited like every other PHI-read content surface — record-level
    # ("which conversation").
    CHAT_CONVERSATION_VIEWED = "chat_conversation_viewed"
    # Read-access event for the conversation-LIST endpoint. The list surfaces no
    # message bodies, but each item carries a title that defaults to
    # "Chat about {patient_display_name}" — an identifier disclosure. So the list
    # is audited patient-scoped ("that the patient's chat index was viewed",
    # resource_id = patient_id), one row per patient per window, NOT one per
    # conversation: granularity matches what was disclosed (titles), not bodies.
    CHAT_CONVERSATION_LIST_VIEWED = "chat_conversation_list_viewed"
    CHAT_CONVERSATION_ARCHIVED = "chat_conversation_archived"
    CHAT_CONVERSATION_PURGED = "chat_conversation_purged"
    CHAT_CHART_PROMOTION = "chat_chart_promotion"
    CHAT_TURN_BLOCKED = "chat_turn_blocked"
    # One row PER TURN, and deliberately exempt from the read-coalescing
    # that collapses repeated clinician reads. The clinician surface
    # audits lifecycle only, because a clinician reading their own
    # patient's chart is the expected case and the per-turn detail lives
    # on the chat_messages rows. On the patient-principal surface the
    # actor is the subject, there is no clinician in the room to be
    # accountable, and "how often did this person talk to it, and when"
    # is itself the reviewable fact — so each turn gets its own row.
    # Metadata only: conversation id and turn sequence, never content.
    CHAT_TURN = "chat_turn"

    # Patient document upload (THERAPY-ak6m.2). UPLOAD_INITIATED fires
    # when a signed PUT URL is minted and the placeholder row inserted;
    # UPLOADED fires after the finalize step verifies the GCS object,
    # validates size/mime, and runs PyMuPDF text extraction. VIEWED,
    # DOWNLOADED, DELETED cover the read-side lifecycle. Payloads carry
    # ids + size + mime + category only — never filename or
    # extracted_text content.
    #
    # *_RESTRICTED variants fire for documents in the therapist_private
    # or psychotherapy_notes categories — both uploader-only, both
    # outside the standard patient-record release path. Splitting the
    # action lets compliance dashboards report on sensitive-document
    # access independently of the chart traffic, and gives us a hook
    # for category-specific retention policies later.
    PATIENT_DOCUMENT_UPLOAD_INITIATED = "patient_document_upload_initiated"
    PATIENT_DOCUMENT_UPLOADED = "patient_document_uploaded"
    PATIENT_DOCUMENT_VIEWED = "patient_document_viewed"
    PATIENT_DOCUMENT_VIEWED_RESTRICTED = "patient_document_viewed_restricted"
    PATIENT_DOCUMENT_DOWNLOADED = "patient_document_downloaded"
    PATIENT_DOCUMENT_DOWNLOADED_RESTRICTED = "patient_document_downloaded_restricted"
    PATIENT_DOCUMENT_DELETED = "patient_document_deleted"
    PATIENT_DOCUMENT_OCR_INVOKED = "patient_document_ocr_invoked"

    # A patient submitted their intake form. The actor is the patient
    # themselves, so this is a write by the subject rather than a
    # clinician disclosure; the payload carries the submission id and the
    # patient id, never any answer.
    PATIENT_INTAKE_SUBMITTED = "patient_intake_submitted"

    # A clinician read a patient's intake submissions. The form carries
    # free text the patient wrote — what brings them in, and anything they
    # said was wrong about their name or date of birth — so opening it is a
    # disclosure of patient-authored content, audited like a chat read
    # rather than like a score. Patient-scoped: ``resource_id`` is the
    # patient id and the payload carries how many submissions came back,
    # never any of their words.
    PATIENT_INTAKE_SUBMISSION_VIEWED = "patient_intake_submission_viewed"

    # A clinician published a version of an intake form. Not a disclosure —
    # a form is the practice's own paperwork and holds nobody's answers — but
    # it is what every submission afterwards will be read against, so which
    # version went live and when is worth having on the record. The payload
    # carries the template and version ids and the number of items, never the
    # questions.
    INTAKE_TEMPLATE_PUBLISHED = "intake_template_published"

    # Secure patient messaging. Both principals write these: a patient
    # starting a thread or sending into one, and a clinician replying. The
    # actor is what ``actor_type`` separates, so the action names say what
    # happened rather than who did it.
    #
    # Payloads carry the thread id, the message id and counts — never the
    # subject a patient typed and never a word of a body. The whole point of
    # the store is that the words live in one place; copying them into the
    # compliance record would put them in two.
    #
    # There is no VIEWED event on the patient's own side, and that is
    # settled rather than missing: a patient reading their own record is not
    # a disclosure to audit. THREAD_VIEWED below is the clinician opening a
    # thread, which is — the same reason ``CHAT_CONVERSATION_VIEWED`` exists.
    PATIENT_MESSAGE_THREAD_CREATED = "patient_message_thread_created"
    PATIENT_MESSAGE_SENT = "patient_message_sent"
    # The patient marking what the practice sent them as read. Audited for
    # non-repudiation: "this was delivered and opened" is the fact a later
    # dispute turns on, and nothing else in the system records it.
    PATIENT_MESSAGE_THREAD_READ = "patient_message_thread_read"
    # A clinician opening a thread — a PHI disclosure, at the granularity of
    # which thread was opened.
    PATIENT_MESSAGE_THREAD_VIEWED = "patient_message_thread_viewed"

    # Companion audio signed-URL upload (additive to the existing
    # multipart /upload-audio surface — companion app migrates at its
    # own pace). INIT fires when channel signed URLs are minted;
    # UPLOADED fires after finalize verifies both channel blobs land
    # and enqueues transcription. The existing SESSION_AUDIO_UPLOADED
    # event remains in use by the multipart path so audit dashboards
    # don't fragment.
    SESSION_AUDIO_UPLOAD_INITIATED = "session_audio_upload_initiated"

    # Companion handoff (companion-thin-client.md). Emitted when a desktop
    # companion redeems a single-use launch intent and the backend
    # discloses the appointment's patient name + video URL to it. Logged
    # at record-level granularity (a patient name is disclosed) with the
    # patient association carried by the `patient=` argument; the `changes`
    # payload never carries patient_name, video_url, or the raw intent_id.
    LAUNCH_INTENT_REDEEMED = "launch_intent_redeemed"

    # Self-pay card payments. These are financial records about a named
    # client, so reading or writing one is a patient-record access and is
    # audited as such. PATIENT_CHARGE_CREATED is emitted when the clinician
    # initiates the charge, not when the processor answers: the event is "this
    # clinician asked to charge this client", which is true the moment the
    # ledger row exists. The outcome lives on that row, and the `changes`
    # payload carries only its opaque id.
    PATIENT_PAYMENT_SETUP_STARTED = "patient_payment_setup_started"
    PATIENT_PAYMENT_METHOD_STORED = "patient_payment_method_stored"
    PATIENT_PAYMENT_METHOD_VIEWED = "patient_payment_method_viewed"
    PATIENT_CHARGE_CREATED = "patient_charge_created"
    PATIENT_CHARGES_VIEWED = "patient_charges_viewed"
    # Reading what a client would be charged discloses their rate — a
    # financial fact about a named person — so the preview is audited like
    # any other read of the record, separately from the charge itself.
    PATIENT_CHARGE_AMOUNT_VIEWED = "patient_charge_amount_viewed"
    # A write-off is a clinician deciding not to collect money a client owes.
    # The `changes` payload carries the ledger row id, the reason, the
    # amount and the claim ids the balance was standing against — never a
    # diagnosis or a payer's member id.
    PATIENT_WRITE_OFF_CREATED = "patient_write_off_created"
    # Money the practice took outside this system — a cheque, cash, a
    # transfer — recorded after the fact. A separate action from
    # PATIENT_CHARGE_CREATED on purpose: that one says a clinician asked a
    # processor to move money and the processor's record corroborates it,
    # while this one is the practice's own unverifiable assertion that it was
    # paid. They answer different questions in an audit, so they must not
    # collapse into one event. The `changes` payload carries the ledger row
    # id, the method and the amount — never the reference text, which is free
    # text a clinician can type anything into.
    PATIENT_PAYMENT_RECORDED = "patient_payment_recorded"

    # Coverage on file. A client's plan — payer, member id, subscriber — is
    # protected health information about a named person, so reading or
    # writing it is a patient-record access and is audited as such. The
    # `changes` payload carries the coverage row id and the payer row id
    # only: never the member id or anything about the subscriber.
    PATIENT_COVERAGE_VIEWED = "patient_coverage_viewed"
    PATIENT_COVERAGE_CREATED = "patient_coverage_created"
    PATIENT_COVERAGE_UPDATED = "patient_coverage_updated"
    PATIENT_COVERAGE_DEACTIVATED = "patient_coverage_deactivated"
    # An eligibility check discloses the client to the payer; one row per check.
    PATIENT_COVERAGE_VERIFIED = "patient_coverage_verified"

    # Claims. A claim carries the client's diagnoses and the subscriber's
    # demographics, so building, reading or moving one is a patient-record
    # access. The `changes` payload carries the claim id, its control number,
    # its state, the payer row id and — for a correction or void — the parent
    # claim id. Never a member id, a diagnosis code or anything about the
    # subscriber.
    CLAIM_CREATED = "claim_created"
    CLAIM_VIEWED = "claim_viewed"
    CLAIM_VALIDATED = "claim_validated"
    CLAIM_CORRECTED = "claim_corrected"
    CLAIM_VOIDED = "claim_voided"
    PATIENT_CLAIMS_VIEWED = "patient_claims_viewed"
    # A remittance whose own numbers disagreed, and what the practice
    # decided to do about the client's bill. The resolution row is the
    # record that a person — named — chose to bill a client an amount the
    # engine had refused to bill them, or chose not to bill it at all. The
    # `changes` payload carries the hold id, the claim, the finding, and
    # the amount written to the ledger; never a name or a diagnosis.
    CLAIM_REMITTANCE_HOLDS_LISTED = "claim_remittance_holds_listed"
    CLAIM_REMITTANCE_HOLD_ACKNOWLEDGED = "claim_remittance_hold_acknowledged"
    CLAIM_REMITTANCE_HOLD_RESOLVED = "claim_remittance_hold_resolved"
    # What a claim still needs a person to do — a rejection to answer, a
    # deadline to beat. These lived on the compliance dashboard until
    # 2026-09, filed as ``claim_*`` compliance items, and were never audited
    # there: that surface is the clinician's own credentials and is exempt.
    # They are about a patient's claim, so on this path they are audited like
    # every other claim read. The `changes` payload carries reminder ids,
    # kinds and the claims they belong to; never a name or a diagnosis.
    CLAIM_REMINDERS_LISTED = "claim_reminders_listed"
    CLAIM_REMINDER_COMPLETED = "claim_reminder_completed"
    # The tracker: every claim the clinician can see, in one read. One row
    # naming the claims it listed (ids and control numbers), like the CSV
    # export does.
    CLAIMS_LISTED = "claims_listed"
    # A status check the clinician asked for: the row names its one claim
    # and where it stood afterwards.
    CLAIM_STATUS_CHECKED = "claim_status_checked"
    # The biller handoff: a range of claims left the practice as a CSV, or
    # one claim as a CMS-1500-layout PDF. A disclosure, so the CSV row names
    # every claim it carried (ids and control numbers, with the range and
    # the count) and the PDF row names its one claim.
    CLAIMS_EXPORTED = "claims_exported"
    CLAIM_EXPORTED = "claim_exported"

    # Superbills. The document hands the client their diagnoses, the
    # services and the practice's identity to pass on to an insurer, so
    # issuing one is a disclosure. The `changes` payload carries the period,
    # the claim, line and charge ids the document was rendered from — or,
    # when it was refused, the codes and field paths of what was missing.
    SUPERBILL_GENERATED = "superbill_generated"
    SUPERBILL_REFUSED = "superbill_refused"

    # Statements. What a client owes, rendered as a document the practice
    # hands them. A disclosure of one client's financial history, so the
    # `changes` payload names the ledger rows it totalled and the balance it
    # printed — never a service, a visit's date or anything clinical.
    STATEMENT_GENERATED = "statement_generated"
    # Every client carrying a balance, in one read — the practice-wide
    # collections view. One row naming the clients it listed and the count,
    # the same granularity as the claims tracker's, rather than one row per
    # client for a screen that discloses them together.
    BALANCES_LISTED = "balances_listed"

    # The practice's own records for a period: everything it billed, or
    # everything on the charge ledger, as a CSV. The disclosure is a whole
    # window rather than one record, so the row says which window and how
    # many rows left — not which clients were in it, because naming every
    # client of a year would put the roster in the audit trail to describe a
    # file that carries client ids and nothing else.
    BILLING_PERIOD_EXPORTED = "billing_period_exported"

    # The clinician's own credential record. Not patient PHI — this is her
    # SSN, date of birth, tax id and bank account — but the most sensitive
    # class the schema holds outside the PHI perimeter, and state SSN and
    # breach-notification law attaches to it. So every DECRYPTION is audited,
    # not just every write: the encrypted columns have one reader
    # (``app.credentialing.government_ids``) and it records each read with the
    # field names it decrypted. The `changes` payload never carries a value,
    # and never the key names ``ssn`` / ``dob`` either — those are in
    # PHI_FIELD_NAMES, so ``_assert_changes_phi_free`` would refuse the row.
    CREDENTIAL_IDENTIFIERS_VIEWED = "credential_identifiers_viewed"
    CREDENTIAL_IDENTIFIERS_UPDATED = "credential_identifiers_updated"
    CREDENTIAL_BANK_ACCOUNT_VIEWED = "credential_bank_account_viewed"
    # A panel moving. Recorded beside the ``payer_participation_events`` row
    # because the two answer different questions: the event row is the
    # clinical-operations history of the panel, this is who moved it and from
    # where. Payload carries the participation id, the payer row id and the
    # from/to statuses — no payer-assigned provider id.
    PAYER_PARTICIPATION_TRANSITIONED = "payer_participation_transitioned"

    # The financial report: aging, payer mix, collections rate and
    # claim-to-payment lag over an explicit window, all computed on read
    # from the ledger and claims. One row per read naming the window and
    # the counts behind each section — ids and amounts only, never a payer
    # mix keyed to a named client.
    BILLING_REPORT_VIEWED = "billing_report_viewed"


class ResourceType(StrEnum):
    """Resource types for audit logging."""

    PATIENT = "patient"
    SESSION = "session"
    APPOINTMENT = "appointment"
    EHR_ROUTE = "ehr_route"
    SELF = "self"
    TENANT_EXPORT = "tenant_export"
    CHAT_CONVERSATION = "chat_conversation"
    PATIENT_DOCUMENT = "patient_document"
    PATIENT_INTAKE_SUBMISSION = "patient_intake_submission"
    INTAKE_PACKET_VERSION = "intake_packet_version"
    PATIENT_MESSAGE_THREAD = "patient_message_thread"
    INVITATION = "invitation"
    CLAIM = "claim"
    CLAIM_EXPORT = "claim_export"
    BILLING_PERIOD_EXPORT = "billing_period_export"
    BILLING_REPORT = "billing_report"
    CREDENTIAL_RECORD = "credential_record"
    PAYER_PARTICIPATION = "payer_participation"


# HIPAA § 164.316(b)(2)(i) — 6-year minimum retention. 7y = margin + matches
# typical state medical-record retention laws.
AUDIT_LOG_RETENTION_DAYS = 2555


# Field names whose *values* must never appear in audit_logs. The set is
# consumed by tests and by AuditRepository.metadata_for_review() to assert
# that the audit table (and any payload derived from it) stays PHI-free.
PHI_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "user_name",
        "user_email",
        "patient_name",
        "first_name",
        "last_name",
        "email",
        "phone",
        "date_of_birth",
        "dob",
        "diagnosis",
        "address",
        "ssn",
        "mrn",
    }
)


# What kind of principal performed an audited action.
#
# ``clinician`` is a signed-in practitioner acting in their own practice.
# ``patient`` is a patient principal acting for themselves — consent decisions
# above all, which is what makes the distinction legally load-bearing rather
# than cosmetic.
# ``anonymous`` is an unauthenticated principal acting through a public surface
# (today: a booking link). For this kind, ``user_id`` names the SCOPE principal
# — the clinician whose RLS context the write happened under — not the actor;
# the actor is identified by ``ip_address`` plus the provenance in ``changes``.
# ``system`` is automated work with no human in the loop: a cron, a queue
# worker, a background agent. ``user_id`` again names the SCOPE principal (the
# clinician whose data was touched), NOT an actor — nobody clicked anything.
# ``actor_component`` says which part of the system acted, and rows of this
# kind should always carry it.
# ``platform_staff`` is an operator of this deployment reading or changing a
# practice's data from outside that practice — support and break-glass access.
# Here ``user_id`` IS the actor: the staff member is individually accountable.
#
# The last two exist because a reader years later has to be able to tell
# "the therapist opened this chart" from "a background job read it to compose
# an email" from "someone at the vendor looked". Folding all three into
# ``clinician`` made the log claim a practitioner did things they never did.
ACTOR_TYPE_CLINICIAN = "clinician"
ACTOR_TYPE_PATIENT = "patient"
ACTOR_TYPE_ANONYMOUS = "anonymous"
ACTOR_TYPE_SYSTEM = "system"
ACTOR_TYPE_PLATFORM_STAFF = "platform_staff"
ACTOR_TYPES: tuple[str, ...] = (
    ACTOR_TYPE_CLINICIAN,
    ACTOR_TYPE_PATIENT,
    ACTOR_TYPE_ANONYMOUS,
    ACTOR_TYPE_SYSTEM,
    ACTOR_TYPE_PLATFORM_STAFF,
)

# Which part of the system acted, for ``actor_type == "system"`` rows.
#
# Deliberately a free string with constants rather than a DB-constrained enum:
# a new background job should not need a schema migration to be able to audit
# itself, and overlays that live outside this package need to name their own
# components without dragging values through here. Consumers treat it as an
# opaque label for filtering. Dotted, stable, and never PHI.
ACTOR_COMPONENT_MAX_LENGTH = 64


@dataclass(frozen=True)
class AuditCursor:
    """A position in one user's audit stream: a timestamp and an entry id.

    The id is a tie-break, not decoration. Rows written inside one
    transaction can share a timestamp to the microsecond, and a cursor on
    timestamp alone would either skip the rest of that group or serve it
    twice — in the one record a user is meant to be able to trust.

    Encoded opaquely so no caller builds one by hand and then depends on
    the shape of it.
    """

    timestamp: datetime
    entry_id: str

    def encode(self) -> str:
        raw = f"{self.timestamp.isoformat()}|{self.entry_id}"
        return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

    @classmethod
    def decode(cls, value: str) -> "AuditCursor":
        """Parse an encoded cursor. Raises ``ValueError`` on anything else."""
        padded = value + "=" * (-len(value) % 4)
        try:
            raw = base64.urlsafe_b64decode(padded.encode()).decode()
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ValueError("cursor is not valid base64url") from exc
        timestamp_part, separator, entry_id = raw.partition("|")
        if not separator or not entry_id:
            raise ValueError("cursor is missing its entry id")
        try:
            timestamp = datetime.fromisoformat(timestamp_part)
        except ValueError as exc:
            # One vocabulary for every way a cursor can be wrong, so a caller
            # can recognise the class without matching on three messages.
            raise ValueError("cursor has an unreadable timestamp") from exc
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return cls(timestamp=timestamp, entry_id=entry_id)

    @classmethod
    def from_entry(cls, entry: "AuditLogEntry") -> "AuditCursor":
        timestamp = datetime.fromisoformat(entry.timestamp)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return cls(timestamp=timestamp, entry_id=entry.id)


@dataclass
class AuditLogEntry:
    """
    Audit log entry for HIPAA compliance tracking.

    PHI-free by design. No denormalized names, emails, or free-text clinical
    data. The `changes` field stores field-name diffs for UPDATE actions
    (e.g. ``{"changed_fields": ["first_name", "diagnosis"]}``) — never the
    old/new values themselves.
    """

    # Auto-generated fields
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=utc_now_iso)
    expires_at: str = field(
        default_factory=lambda: (
            (datetime.now(UTC) + timedelta(days=AUDIT_LOG_RETENTION_DAYS))
            .isoformat()
            .replace("+00:00", "Z")
        )
    )

    # Who performed the action.
    #
    # ``user_id`` is the actor identifier as recorded; ``actor_type`` says what
    # KIND of actor it names. Both ids are uuids, so without the discriminator a
    # row cannot answer "was this the clinician or the patient?" without joining
    # two tables and hoping exactly one matches — and this is the six-year
    # record, read years later by someone in a dispute.
    #
    # Defaults to ``clinician`` so every row written before this existed, and
    # every caller that does not set it, keeps exactly the meaning it had.
    user_id: str = ""
    actor_type: str = ACTOR_TYPE_CLINICIAN
    # Which part of the system acted. Only meaningful for ``system`` rows,
    # where ``user_id`` names the scope rather than an actor; ``None`` for
    # every human kind, whose actor is already named by ``user_id``.
    actor_component: str | None = None

    # What action was performed
    action: str = ""  # AuditAction value
    resource_type: str = ""  # ResourceType value
    resource_id: str = ""

    # Opaque context IDs (non-PHI)
    patient_id: str | None = None
    session_id: str | None = None

    # Request context
    ip_address: str | None = None
    user_agent: str | None = None

    # Non-PHI structured data only: field-name diffs, counts, enum transitions.
    # Callers must never put PHI values here. AuditService enforces this via
    # the PHI_FIELD_NAMES assertion.
    changes: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditLogEntry":
        """Create AuditLogEntry from dictionary."""
        return cls(
            id=data["id"],
            timestamp=data["timestamp"],
            expires_at=data["expires_at"],
            user_id=data["user_id"],
            actor_type=data.get("actor_type", ACTOR_TYPE_CLINICIAN),
            action=data["action"],
            resource_type=data["resource_type"],
            resource_id=data["resource_id"],
            patient_id=data.get("patient_id"),
            session_id=data.get("session_id"),
            ip_address=data.get("ip_address"),
            user_agent=data.get("user_agent"),
            changes=data.get("changes"),
        )
