# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Read the intake forms filled in before packets, as packet submissions.

The engine used to ask one fixed set of questions: confirm the name and the
date of birth on the record, say what brings you in, answer the PHQ-9 and
the GAD-7. Practices can now build their own form, and the fixed one became
version 1 of the "Intake" template every schema is seeded with — the same
four questions, in the same order, under a model that can grow.

What is left is the forms people already filled in. They sit in
``patient_intake_submissions`` as one JSON body apiece, which means a chart
has two places to look and a later feature — a correction, an export, a
comparison between two administrations — has to know about both. This
command writes each of those bodies out as an assignment on the default
form, so there is one shape to read.

**A command, not a migration**, and that was a decision rather than an
accident. A revision runs unattended on every practice on the way to
serving traffic; this reads a clinical record, writes a second copy of it,
and is worth an operator looking at the counts before and after. It also
has to run AFTER the deploy that ships the columns it writes, not during
it.

**Idempotent, and not by remembering.** Each adopted assignment carries the
``legacy_submission_id`` it came from, under a unique index, so running the
command twice cannot produce two copies even if two operators run it at the
same moment. The second run reports the same counts as the first.

**Nothing is scored again.** The submission was scored into
``outcome_measures`` when it arrived, so the measures are already on the
chart. Writing them a second time would double every trend the chart draws.
The instrument answers still come across, because they belong to the form.

**The legacy table is left exactly as it is.** Dropping it is a separate
decision, taken once a deployment has looked at what this produced. Until
then both exist, and the old one is the one that can be checked against.

**Row security is suspended for the duration and put back.** Every table
this touches is force-RLS'd and scoped to one patient at a time, which is
right for a request and impossible for a command that has to read a whole
practice's submissions in one pass. So the three tables' row-security flags
are read, turned off, and restored to exactly what they were — the pattern
tenant revisions that rewrite patient rows already use (see
``c1b7e4a92d53``). All of it inside one transaction, so a failure anywhere
puts the flags back on the way out rather than leaving a table open.
Nothing here asks for a role with ``BYPASSRLS``: the command runs as the
schema's owner, which is the role the application already connects as.

Run it after deploying::

    python -m app.bin.adopt_intake_submissions            # every practice
    python -m app.bin.adopt_intake_submissions --schema practice_abc123
    python -m app.bin.adopt_intake_submissions --dry-run  # counts only
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..db import PLATFORM_SCHEMA, _validate_schema_name
from ..db.intake_seed import DEFAULT_PACKET_NAME
from ..intake.receipts import new_receipt_code

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger("pablo.adopt_intake_submissions")

#: The item keys the seeded default form uses, mapped to the part of a
#: legacy payload that answers them. Keyed by ``key`` rather than by
#: position: a practice may have reordered or renamed its own form, and an
#: item that is no longer there is skipped rather than guessed at.
_DEMOGRAPHICS_KEY = "demographics"
_REASON_KEY = "reason"

#: How many receipt codes to try per row before giving up. Same reasoning
#: as the submit path: a collision needs two draws from thirty to the
#: eighth to match, so a bounded retry is a formality that cannot spin.
_RECEIPT_ATTEMPTS = 5

#: The tables this command reads and writes across every patient in a
#: practice at once, and so the ones whose row security it suspends.
_TABLES = (
    "patient_intake_submissions",
    "patient_intake_assignments",
    "patient_intake_responses",
)


@dataclass(frozen=True)
class PracticeResult:
    """What the command did to one practice schema."""

    schema: str
    submissions: int
    adopted: int
    already_adopted: int
    skipped: int

    def line(self) -> str:
        return (
            f"{self.schema}: {self.submissions} submission(s), "
            f"{self.adopted} adopted, {self.already_adopted} already adopted, "
            f"{self.skipped} skipped"
        )


def adopt_practice(engine: Engine, schema: str, *, dry_run: bool = False) -> PracticeResult:
    """Adopt every legacy submission in one practice schema.

    Returns counts rather than raising on a row it cannot place: a practice
    that edited its default form away still has readable submissions, and
    stopping the whole fan-out over one of them would leave the practices
    behind it untouched.
    """
    _validate_schema_name(schema)
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
            suspended = _suspend_row_security(conn, schema)
            result = _adopt_within(conn, schema, dry_run=dry_run)
            _restore_row_security(conn, schema, suspended)
        except Exception:
            transaction.rollback()
            raise
        if dry_run:
            transaction.rollback()
        else:
            transaction.commit()
        return result


def _adopt_within(conn: Connection, schema: str, *, dry_run: bool) -> PracticeResult:
    """The work itself, on a connection that can already see the rows."""
    version_id, items = _default_form(conn)
    submissions = _legacy_submissions(conn)
    if version_id is None:
        logger.warning(
            "%s: no published default form, so %d submission(s) stay where they are",
            schema,
            len(submissions),
        )
        return PracticeResult(schema, len(submissions), 0, 0, len(submissions))

    counts = {"adopted": 0, "already": 0, "skipped": 0}
    for row in submissions:
        counts[_adopt_one(conn, version_id, items, row, dry_run=dry_run)] += 1
    return PracticeResult(
        schema,
        len(submissions),
        counts["adopted"],
        counts["already"],
        counts["skipped"],
    )


def adopt_all(engine: Engine, *, dry_run: bool = False) -> list[PracticeResult]:
    """Adopt across every live practice schema, including a solo install's."""
    from ..db.migrate_tenants import list_active_practice_registry  # noqa: PLC0415

    results = []
    for schema, _practice_id in list_active_practice_registry(engine):
        results.append(adopt_practice(engine, schema, dry_run=dry_run))
    return results


# ---------------------------------------------------------------------------
# Row security, off and back on
# ---------------------------------------------------------------------------


def _suspend_row_security(conn: Connection, schema: str) -> dict[str, tuple[bool, bool]]:
    """Turn row security off on the three tables, returning what it was.

    The flags are read first and handed back rather than assumed, because
    "put it back the way it was" and "put it back the way the code thinks
    it should be" are different promises, and only the first one is safe to
    make about somebody else's database.
    """
    observed: dict[str, tuple[bool, bool]] = {}
    for table in _TABLES:
        row = conn.execute(
            text(
                "SELECT c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :s AND c.relname = :t"
            ),
            {"s": schema, "t": table},
        ).first()
        if row is None:
            continue
        observed[table] = (bool(row[0]), bool(row[1]))
        conn.execute(text(f'ALTER TABLE "{schema}"."{table}" NO FORCE ROW LEVEL SECURITY'))
        conn.execute(text(f'ALTER TABLE "{schema}"."{table}" DISABLE ROW LEVEL SECURITY'))
    return observed


def _restore_row_security(
    conn: Connection, schema: str, observed: dict[str, tuple[bool, bool]]
) -> None:
    """Put back exactly the flags :func:`_suspend_row_security` saw."""
    for table, (enabled, forced) in observed.items():
        if enabled:
            conn.execute(text(f'ALTER TABLE "{schema}"."{table}" ENABLE ROW LEVEL SECURITY'))
        if forced:
            conn.execute(text(f'ALTER TABLE "{schema}"."{table}" FORCE ROW LEVEL SECURITY'))


# ---------------------------------------------------------------------------
# One submission
# ---------------------------------------------------------------------------


def _adopt_one(
    conn: Connection,
    version_id: str,
    items: Mapping[str, str],
    submission: Mapping[str, object],
    *,
    dry_run: bool,
) -> str:
    """Write one legacy submission out as an accepted assignment.

    Returns ``"adopted"``, ``"already"`` when this submission has one
    already, or ``"skipped"`` when its answers do not fit the practice's
    current default form.
    """
    submission_id = str(submission["id"])
    if _already_adopted(conn, submission_id):
        return "already"

    payload = submission["payload"] if isinstance(submission["payload"], dict) else {}
    answers = _answers_from(payload, items)
    if not answers:
        logger.warning(
            "submission %s answers nothing on the current default form; left as it is",
            submission_id,
        )
        return "skipped"

    if dry_run:
        return "adopted"

    assignment_id = str(uuid.uuid4())
    submitted_at = submission["submitted_at"]
    patient_id = str(submission["patient_id"])
    _insert_assignment(
        conn,
        {
            "id": assignment_id,
            "pid": patient_id,
            "vid": version_id,
            "at": submitted_at,
            "legacy": submission_id,
        },
    )
    for item_id, value in answers.items():
        conn.execute(
            text(
                "INSERT INTO patient_intake_responses "
                "(id, assignment_id, patient_id, item_id, value, draft, "
                "superseded_by, created_at, updated_at) "
                "VALUES (CAST(:id AS uuid), CAST(:aid AS uuid), CAST(:pid AS uuid), "
                "CAST(:iid AS uuid), CAST(:value AS jsonb), FALSE, NULL, :at, :at)"
            ),
            {
                "id": str(uuid.uuid4()),
                "aid": assignment_id,
                "pid": patient_id,
                "iid": item_id,
                "value": json.dumps(value),
                "at": submitted_at,
            },
        )
    return "adopted"


def _insert_assignment(conn: Connection, row: dict[str, object]) -> None:
    """Insert the assignment row, retrying only on a receipt collision.

    ``accepted`` rather than ``submitted``: these forms were handed in and
    acted on long before there was a review cycle to put them through, and
    recording them as still waiting for one would fill every practice's
    queue with work that is already done. ``accepted_at`` is the moment the
    patient submitted, because that is the only moment the record knows.
    """
    for attempt in range(_RECEIPT_ATTEMPTS):
        savepoint = conn.begin_nested()
        try:
            conn.execute(
                text(
                    "INSERT INTO patient_intake_assignments "
                    "(id, patient_id, version_id, status, assigned_by, assigned_at, "
                    "submitted_at, accepted_at, withdrawn_at, receipt_code, "
                    "legacy_submission_id, updated_at) "
                    "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), CAST(:vid AS uuid), "
                    "'accepted', NULL, :at, :at, :at, NULL, :receipt, "
                    "CAST(:legacy AS uuid), :at)"
                ),
                {**row, "receipt": new_receipt_code()},
            )
        except IntegrityError:
            # A receipt already taken, or this submission adopted by a run
            # happening at the same moment. Either way the answer is to
            # draw another code and try once more; a second refusal on the
            # legacy id will exhaust the attempts and surface.
            savepoint.rollback()
            if attempt == _RECEIPT_ATTEMPTS - 1:
                raise
            continue
        savepoint.commit()
        return


def _answers_from(
    payload: Mapping[str, object], items: Mapping[str, str]
) -> dict[str, dict[str, object]]:
    """Map one legacy payload onto the default form's item ids.

    Reads defensively: the column is a record of what a form sent, and a
    body written by an earlier version of that form is a normal thing to
    find rather than an error. A question the practice has since removed
    from its form simply has nowhere to put its answer.
    """
    answers: dict[str, dict[str, object]] = {}

    if (item_id := items.get(_DEMOGRAPHICS_KEY)) is not None:
        corrections = payload.get("corrections")
        answers[item_id] = {
            # True for a body written before the form asked, so an old
            # submission reads as "nothing flagged" rather than as a
            # correction nobody made. Same default the chart already uses.
            "name_confirmed": bool(payload.get("name_confirmed", True)),
            "dob_confirmed": bool(payload.get("dob_confirmed", True)),
            "corrections": str(corrections) if corrections else None,
        }

    reason_text = payload.get("reason_text")
    if (item_id := items.get(_REASON_KEY)) is not None and reason_text:
        answers[item_id] = {"text": str(reason_text)}

    instruments = payload.get("instruments")
    if isinstance(instruments, dict):
        for code, scores in instruments.items():
            item_id = items.get(str(code))
            if item_id is not None and isinstance(scores, dict):
                answers[item_id] = {"item_scores": {str(k): int(v) for k, v in scores.items()}}

    return answers


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _default_form(conn: Connection) -> tuple[str | None, dict[str, str]]:
    """The newest published version of the default form, and its item ids.

    By name, because that is what identifies it: the seed creates exactly
    one template called "Intake" per schema, and a practice that renamed it
    has a form of its own that these submissions should not be filed under.
    """
    version_id = conn.execute(
        text(
            "SELECT v.id::text FROM intake_packet_versions v "
            "JOIN intake_packet_templates t ON t.id = v.template_id "
            "WHERE t.name = :name AND t.archived_at IS NULL "
            "AND v.published_at IS NOT NULL "
            "ORDER BY v.version DESC LIMIT 1"
        ),
        {"name": DEFAULT_PACKET_NAME},
    ).scalar()
    if version_id is None:
        return None, {}

    items = conn.execute(
        text(
            "SELECT key, id::text FROM intake_item_definitions "
            "WHERE version_id = CAST(:vid AS uuid)"
        ),
        {"vid": version_id},
    ).all()
    return str(version_id), {str(key): str(item_id) for key, item_id in items}


def _legacy_submissions(conn: Connection) -> list[Mapping[str, object]]:
    rows = conn.execute(
        text(
            "SELECT id::text AS id, patient_id::text AS patient_id, "
            "submitted_at, payload FROM patient_intake_submissions "
            "ORDER BY submitted_at"
        )
    ).mappings()
    return [dict(row) for row in rows]


def _already_adopted(conn: Connection, submission_id: str) -> bool:
    found = conn.execute(
        text(
            "SELECT 1 FROM patient_intake_assignments "
            "WHERE legacy_submission_id = CAST(:sid AS uuid)"
        ),
        {"sid": submission_id},
    ).scalar()
    return found is not None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    parser.add_argument(
        "--schema",
        help="One practice schema. Omit to run across every live practice.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be adopted and write nothing.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    from ..db import get_engine  # noqa: PLC0415 — needs settings, which need an env

    engine = get_engine()
    results = (
        [adopt_practice(engine, args.schema, dry_run=args.dry_run)]
        if args.schema
        else adopt_all(engine, dry_run=args.dry_run)
    )
    for result in results:
        logger.info("%s", result.line())
    logger.info(
        "%d practice(s), %d adopted, %d already adopted, %d skipped%s",
        len(results),
        sum(r.adopted for r in results),
        sum(r.already_adopted for r in results),
        sum(r.skipped for r in results),
        " (dry run, nothing written)" if args.dry_run else "",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover — the command's own entry point
    sys.exit(main())


__all__ = ["PracticeResult", "adopt_all", "adopt_practice", "main"]
