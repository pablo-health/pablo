# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The three reads that see the whole practice: who its clients are, which
outside event is already booked, and what a claim for any session needs.

A client belongs to the practice, not to one clinician, so matching a
calendar event or a feed code must see every chart in the practice — or it
makes a second chart for a colleague's client. Row-level security shows a
clinician only the charts they hold a grant on, which is right for every
other read and wrong for this one.

``practice_client_directory()`` is that exception, and it is narrow: for
each live chart in its own schema it returns the id, first and last name,
date of birth, email, and the ids of the clinicians holding a grant. Nothing
else — no notes, no other contact details, no diagnoses.

``practice_outside_appointment(source, calendar_id, event_id)`` is the
second, narrower still: the id of the live appointment following one
outside event, wherever in the practice it is. Two clinicians following one
shared calendar must never book the same session twice, and the second to
answer links to the first's appointment; the appointment table shows each
clinician only their own rows, so the link needs this.

``practice_claim_source(appointment_id)`` is what claim assembly reads for
one visit, whoever held it: the visit's codes, the client's claim
demographics, the active coverage, the visit type's fee and the treating
clinician's NPI and taxonomy. It lets someone who bills for the practice
build a claim for a colleague's session without a grant on the client. Its
own guard is the armed clinician, like the others'; who may bill is decided
in the app, by ``app.billing_access.can_bill``, before the call. The
practice owner is recorded on the platform practice row by email, which this
schema cannot see, so the function cannot make that check itself.

**Why they need a role of their own.** The app's role owns the tenant tables
and they are ``FORCE ROW LEVEL SECURITY``, so a ``SECURITY DEFINER`` function
owned by that role runs under the very policies it is meant to see past.
Instead the functions are owned by ``pablo_practice_directory``: a role that
cannot log in, cannot bypass RLS, may read only the columns above, and is
admitted by one SELECT policy on each table it reads, naming it and no one
else. The app's role is a member that can hand it ownership but does not
inherit its privileges, so every ordinary read keeps exactly the policy it
had.

**Where the access comes from.** The tenant template carries all of it, so a
new practice has it once the template is applied: the read policies,
captured like every other policy, and the grants, pinned paths and ownership,
written out by :func:`directory_access_sql`. Provisioning runs nothing else
for it, and nothing sets any of it when the app starts.

:func:`apply_practice_directory_access` does the same work as code, for the
places a schema's objects are created or moved outside provisioning: the
revisions that add each function (a practice that already exists gets its
access there), a practice restored from an archive (the dump keeps neither
owners nor grants), and the one-time conversion of a single-practice install
(a renamed schema leaves each function's path naming the old one). The migrate
fan-out still calls it after its reconcile, which does not manage the
directory's read policy. It is also what the template's capture runs, so the
file and the code cannot say different things. A later change to a grant, an
owner or a path ships as a revision that calls it.

**Changing a function later.** Once a schema's function belongs to the
directory role, the role that runs migrations no longer owns it and cannot
``CREATE OR REPLACE`` or ``DROP`` it directly ("must be owner of function").
A revision that changes the body does it as the owner, inside
:func:`as_directory_owner`::

    with as_directory_owner(db, schema):
        db.execute(text(create_directory_function_sql(schema)))

That grants the role ``CREATE`` on the schema, ``SET ROLE``\\s to it, and on
the way out resets the role and revokes ``CREATE`` again, so the role keeps
nothing it did not have. :func:`drop_directory_function` is the same recipe
for a downgrade. A schema whose function is still owned by the migrator —
the template schema, or one the access pass has not reached — takes the
statement directly.
"""

from .practice_directory_access import (
    apply_practice_directory_access,
    directory_access_sql,
)
from .practice_directory_definitions import (
    APPOINTMENT_COLUMNS,
    CLAIM_SOURCE_FUNCTION,
    DIRECTORY_FUNCTION,
    DIRECTORY_POLICY,
    DIRECTORY_ROLE,
    GRANT_COLUMNS,
    OUTSIDE_APPOINTMENT_FUNCTION,
    PATIENT_COLUMNS,
)
from .practice_directory_functions import (
    as_directory_owner,
    create_claim_source_function,
    create_directory_function,
    create_directory_function_sql,
    create_outside_appointment_function,
    directory_function_owner,
    drop_claim_source_function,
    drop_directory_function,
    drop_outside_appointment_function,
    outside_appointment_function_sql,
    require_directory_role,
)

__all__ = [
    "APPOINTMENT_COLUMNS",
    "CLAIM_SOURCE_FUNCTION",
    "DIRECTORY_FUNCTION",
    "DIRECTORY_POLICY",
    "DIRECTORY_ROLE",
    "GRANT_COLUMNS",
    "OUTSIDE_APPOINTMENT_FUNCTION",
    "PATIENT_COLUMNS",
    "apply_practice_directory_access",
    "as_directory_owner",
    "create_claim_source_function",
    "create_directory_function",
    "create_directory_function_sql",
    "create_outside_appointment_function",
    "directory_access_sql",
    "directory_function_owner",
    "drop_claim_source_function",
    "drop_directory_function",
    "drop_outside_appointment_function",
    "outside_appointment_function_sql",
    "require_directory_role",
]
