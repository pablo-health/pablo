# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Importing a records-system export into a practice.

A practice moving onto Pablo, or trying it alongside the system it still
uses, arrives with an export archive: contact cards, notes as PDFs, billing
documents, a secure-message log, uploaded files. This package reads such an
archive into typed records (``readers``), works out what each record would
become in the chart and what needs a human decision (``preview``), lands the
records through the same services the application uses (``apply``), and
keeps a ledger keyed by the source system's own record ids so a second run
of the same archive is exact and every run can be undone (``ledger``).

Nothing here guesses. A field comes from the archive's fixed layout or it
is reported; a record that could belong to more than one client is put in
front of the practice to assign; a note body is landed verbatim, never
restructured.
"""
