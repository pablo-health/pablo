# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient portal sign-in: invitations, step-up, sessions.

Every patient-facing surface in the engine — intake, secure messaging,
appointments, self-booking — authenticates through
``app.auth.patient_context``. This package is the front door that seam has
been waiting for: the thing that turns a person holding an emailed link
into a resolvable patient principal.

The flow, in the order it happens:

1. A clinician invites one patient. The engine mints a single-use invite
   token, emails it as a magic link, and texts a one-time code.
2. The patient opens the link and enters the code. Both factors together
   redeem into a patient-session token.
3. That session token is the patient's bearer credential.
   :class:`~app.portal.resolver.PortalSessionResolver` resolves it on every
   subsequent request, checking a server-side row as well as the signature,
   which is what makes a clinician's revoke take effect immediately.

Two factors on two channels is the point, not ceremony: a link that reached
the wrong inbox is one factor in a stranger's hands, and § 164.312(d)
requires more than that before a chart opens.

Layout, roughly inner to outer:

* ``errors`` / ``tokens`` / ``otp`` / ``store`` / ``service`` — the pure
  core. No settings, no SQLAlchemy, no HTTP; a unit test imports these
  alone.
* ``db_store`` — the two per-tenant tables behind ``store``'s protocols.
* ``delivery`` / ``adapters`` — the ports the two channels are reached
  through, and the adapters the engine ships for them.
* ``factory`` / ``tenant_gateway`` — wiring: settings into a service, and a
  tenant-scoped transaction for the routes that have no principal to
  inherit one from.
* ``routes`` / ``resolver`` — the HTTP surface, and the front door.
"""
