# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Static guardrail: PHI-touching API routes must declare and call AuditService.

PHI access without an audit entry is a HIPAA § 164.312(b) gap. This is a
pure-stdlib AST check — no app import, no DB, no network — so it runs fast and
deterministically anywhere: CI (via ``test_route_audit_guardrails.py``, which
delegates here), a git pre-commit hook, or a Claude Code PostToolUse hook fired
when a route file is edited.

Four rules, all enforced against the *mounted* path (router prefix + decorator
arg) so prefixed routers (``APIRouter(prefix="/api/patients")``) are not blind
spots:

  1. No route handler may use an underscore-prefixed ``_audit`` /
     ``_http_request`` parameter — that silences every linter and is a silent
     audit bypass.
  2. A handler that injects the tenant ``audit: AuditService`` must actually
     call ``audit.*`` — otherwise the injection is dead weight and the access
     is unaudited.
  3. **Fail-closed.** EVERY ``@router`` handler must either inject+call the
     tenant ``AuditService`` OR be explicitly classified non-PHI in
     ``AUDIT_EXEMPT_NON_PHI_ROUTES`` with a one-line reason. A route at an
     unrecognized path is NOT assumed safe — silence is a violation, not a
     pass. (Marker matching is kept as rule 4's backstop, not the gate.)
  4. A handler whose mounted path matches a PHI marker must inject the tenant
     ``AuditService`` and may NEVER be lazily exempted: it can only appear in
     the small, heavily-reviewed ``AUDIT_EXEMPT_PHI_ROUTES`` list, never in
     ``AUDIT_EXEMPT_NON_PHI_ROUTES``. The engine treats a marker-matching entry
     in the non-PHI list as a hard config error, so a lazy exemption cannot be
     used to dodge a PHI surface.

Note on the two ``*AuditService`` types: only the *tenant* ``AuditService``
(writes per-tenant ``audit_logs``, the HIPAA § 164.312(b) record) satisfies
this check. ``PlatformAuditService`` (the PHI-free cross-tenant ops stream) is
deliberately NOT accepted — a PHI route that wired the platform sink instead of
the tenant sink would otherwise pass while writing to the wrong, PHI-free log.

Run: ``python backend/scripts/check_route_audit.py`` (exits 1 with the list of
violations, 0 if clean). Optional args are file paths; any that are not under
``app/routes/`` are ignored, so it is safe to pass an edited file straight
through from a hook.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent

# Route modules live under app/routes/ in the OSS engine and are scattered
# across saas/**/ in the SaaS overlay. Auto-detect whichever roots exist so
# the identical engine is portable to both repos; the per-repo copy only tunes
# PHI_PATH_MARKERS and AUDIT_EXEMPT_PHI_ROUTES below.
ROUTE_ROOTS: tuple[Path, ...] = tuple(
    d for d in (_BACKEND / "app" / "routes", _BACKEND / "saas") if d.is_dir()
)

# Path substrings that signal a route touches PHI or PHI-adjacent data.
# Extend this list whenever a new PHI surface is added.
PHI_PATH_MARKERS: tuple[str, ...] = (
    "/patients",
    "/sessions",
    "/appointments",
    "/transcript",
    "/audio",
    "/soap",
    "/notes",
    "/resolve-client",
    "/import-clients",
)

FORBIDDEN_UNDERSCORE_PARAMS: frozenset[str] = frozenset({"_audit", "_http_request"})

HTTP_METHODS: frozenset[str] = frozenset({"get", "post", "patch", "put", "delete"})

# List endpoints that match a PHI path marker but deliberately do NOT audit.
# These return only scheduling metadata (times, status, patient association,
# free-text annotation) — not clinical content or patient identifiers — and
# each has a detail endpoint that audits the per-record content read. A
# list-level row recording only a count carries no forensic value.
#
# NOTE: list endpoints that return content — GET /api/sessions (embeds the
# SOAP note), the patient-notes list (every note body), and GET /api/patients
# (diagnosis + DOB/email/phone per patient) — are NOT exempt. Each emits a
# per-record *_viewed event for every record it returns. The axis is "does
# this return clinical content / identifiers", not "is it a list".
#
# Keyed by (method, mounted-path) — i.e. router prefix + decorator arg.
AUDIT_EXEMPT_PHI_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        # internal_transcription.py — the transcript-upload audit is emitted
        # inside process_transcription_result (the single completion funnel),
        # not on the route signature; both handlers delegate to it.
        ("post", "/api/internal/transcription-complete"),  # audited in helper
        ("post", "/api/internal/transcription-poll"),  # delegates to the audited helper
        # users.py — practice-level retention *policy* (a day count), not
        # audio content; matches the "/audio" marker on path text alone.
        ("get", "/api/users/me/practice/audio-retention"),
        ("put", "/api/users/me/practice/audio-retention"),
    }
)

# Fail-closed classification of every handler whose mounted path carries NO PHI
# marker but which still does not audit. Each entry is a deliberate "this route
# touches no patient PHI" decision with a one-line reason. A handler that is
# neither audited nor listed here is a violation — that is what makes the check
# fail-closed (an unrecognized route is never silently assumed safe).
#
# HARD RULE: nothing in here may match a PHI_PATH_MARKER. A marker-matching path
# that genuinely returns only metadata belongs in the reviewed
# AUDIT_EXEMPT_PHI_ROUTES above, never here. ``_exempt_config_violations``
# enforces this at runtime so the list can't be used to dodge a PHI surface.
#
# Keyed by (method, mounted-path) — i.e. router prefix + decorator arg.
AUDIT_EXEMPT_NON_PHI_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        # internal_transcription.py — Cloud Tasks worker that hands audio already
        # audited at upload (SESSION_AUDIO_UPLOADED) to the transcription
        # provider; discloses nothing to a user. The transcript that results is
        # audited on the completion path.
        ("post", "/api/internal/assemblyai-submit"),  # provider submit worker, no disclosure
        # admin.py — operates on therapist accounts / invitees, never patient data
        ("get", "/api/admin/users"),  # admin lists therapist accounts
        ("patch", "/api/admin/users/{user_id}/disable"),  # disables a therapist account
        ("patch", "/api/admin/users/{user_id}/enable"),  # re-enables a therapist account
        ("get", "/api/admin/allowlist"),  # lists prospective-therapist invite emails
        ("post", "/api/admin/allowlist"),  # adds a therapist invite email
        ("delete", "/api/admin/allowlist/{email}"),  # removes a therapist invite email
        # admin_pentest.py — synthetic pentest tenants / platform-audit only, no real PHI
        ("post", "/api/admin/pentest/tenant"),  # provisions a synthetic pentest tenant
        ("delete", "/api/admin/pentest/tenant/{tenant_id}"),  # tears down a pentest tenant
        ("post", "/api/admin/pentest/audit"),  # emits a PlatformAudit pentest-run row, PHI-free
        # auth.py / ext_auth.py — auth tokens and boolean checks only
        ("post", "/api/auth/native/code"),  # mints a short-lived native auth code
        ("post", "/api/auth/native/exchange"),  # exchanges code for auth tokens
        ("get", "/api/auth/session"),  # caller's own idle-session liveness, no PHI
        ("post", "/api/auth/session/touch"),  # refreshes caller's own idle heartbeat, no PHI
        ("post", "/api/ext/auth/check-allowlist"),  # allowlist membership check
        ("post", "/api/ext/auth/check-status"),  # account disabled-status check
        # passkey.py — WebAuthn ceremonies: authenticator metadata + token mint, no PHI
        ("post", "/api/auth/passkey/register/begin"),  # issues registration options
        ("post", "/api/auth/passkey/register/finish"),  # stores enrolled authenticator
        ("post", "/api/auth/passkey/authenticate/begin"),  # issues authentication options
        ("post", "/api/auth/passkey/authenticate/finish"),  # verifies assertion, mints token
        ("get", "/api/auth/passkey/credentials"),  # lists own authenticator metadata + labels
        ("delete", "/api/auth/passkey/credentials/{credential_id}"),  # soft-revokes own passkey
        ("post", "/api/auth/passkey/recovery-code/redeem"),  # spends a backup code, mints token
        # chat.py — context-preview manifest is ids/dates/counts only, PHI-free by design
        ("post", "/api/chat/conversations/preview"),  # context-preview manifest, no PHI
        # launch.py — issues a single-use intent for an appointment the caller
        # already holds; discloses no patient data (the redeem step, which does
        # disclose the patient name, IS audited as launch_intent_redeemed)
        ("post", "/api/launch/intent"),  # mints opaque launch intent, no PHI disclosed
        # payment_webhooks.py — signature-verified processor callback. It moves
        # a ledger row's status from an event the processor signed; there is no
        # authenticated principal to attribute an access to, and it discloses
        # nothing to anybody. The clinician-initiated side of every charge IS
        # audited, on the routes in patient_payments.py.
        ("post", "/api/webhooks/payments/stripe"),  # processor callback, no disclosure
        # booking_links.py — owner's own link metadata (slug/copy/duration), no patient data
        ("post", "/api/booking-links"),  # creates a booking link
        ("get", "/api/booking-links"),  # lists caller's own booking links
        ("patch", "/api/booking-links/{link_id}"),  # updates link copy/duration/active
        ("delete", "/api/booking-links/{link_id}"),  # deletes a booking link
        # public_booking.py — public display card + free/busy only; the booking
        # POST (which creates patient + appointment PHI) IS audited, as an
        # anonymous actor within the owner's scope
        ("get", "/api/public/booking-links/{slug}"),  # link display card, no PHI
        ("get", "/api/public/booking-links/{slug}/slots"),  # free slots, no PHI
        # compliance.py — therapist's own compliance checklist (license/insurance/training)
        ("get", "/api/compliance"),  # therapist's own compliance items
        ("get", "/api/compliance/templates"),  # compliance template catalog
        ("post", "/api/compliance"),  # creates a therapist compliance task
        ("post", "/api/compliance/{item_id}/complete"),  # marks therapist task complete
        ("put", "/api/compliance/{item_id}"),  # updates therapist compliance task
        ("delete", "/api/compliance/{item_id}"),  # deletes therapist compliance task
        # compliance.py — evidence documents attached to compliance items (own credentials, no PHI)
        (
            "post",
            "/api/compliance/{item_id}/documents",
        ),  # uploads evidence doc for therapist's own item
        (
            "get",
            "/api/compliance/{item_id}/documents",
        ),  # lists evidence docs for therapist's own item
        ("get", "/api/compliance/documents/{document_id}/file"),  # streams evidence doc bytes
        ("delete", "/api/compliance/documents/{document_id}"),  # removes evidence doc
        # ehr_routes.py — EHR UI-navigation config (selectors/steps), no patient data
        ("get", "/api/ehr-routes/{ehr_system}"),  # navigation route config
        ("patch", "/api/ehr-routes/{ehr_system}/steps/{step_index}"),  # updates a nav step
        # ical_sync.py — feed connection metadata only (the sync READ itself is audited)
        ("get", "/api/ical-sync/status"),  # feed connection metadata
        ("post", "/api/ical-sync/configure"),  # validates feed URL + event count
        ("delete", "/api/ical-sync/{ehr_system}"),  # disconnects a feed
        # note_types.py — note-type catalog/config
        ("get", "/api/note-types"),  # note-type catalog
        ("get", "/api/note-types/{key}"),  # single note-type definition
        # scheduling.py — therapist's own availability/OAuth, no client attached
        ("get", "/api/availability/rules"),  # therapist availability rules
        ("post", "/api/availability/rules"),  # creates an availability rule
        ("patch", "/api/availability/rules/{rule_id}"),  # updates an availability rule
        ("delete", "/api/availability/rules/{rule_id}"),  # deletes an availability rule
        ("post", "/api/availability/rules/parse"),  # NL rule proposals, no client attached
        ("get", "/api/availability/slots"),  # open free-slot times, no client
        ("post", "/api/availability/check"),  # conflict check, rule messages only
        ("get", "/api/scheduling/policy"),  # practice booking policy; no patient data
        ("patch", "/api/scheduling/policy"),  # practice booking policy; no patient data
        # practice_billing.py — the practice's own billing identity (legal
        # name, tax id, billing NPI, address); no patient data. The tax id
        # is encrypted at rest and the response never carries more than its
        # last 4 digits, so there is nothing here to disclose either.
        ("get", "/api/practice/billing-profile"),  # practice billing identity; no patient data
        ("patch", "/api/practice/billing-profile"),  # practice billing identity; no patient data
        # coverage.py — the practice's payer list (names, electronic payer
        # ids, filing deadlines); no client attached. The per-client coverage
        # routes in the same file live under /api/patients and are audited.
        ("get", "/api/payers"),  # practice payer list; no patient data
        ("post", "/api/payers"),  # adds a payer; no patient data
        ("patch", "/api/payers/{payer_row_id}"),  # edits a payer; no patient data
        ("get", "/api/appointment-types"),  # practice-level fee defaults, no client
        ("post", "/api/appointment-types"),  # creates an appointment type
        ("patch", "/api/appointment-types/{appointment_type_id}"),  # updates an appointment type
        ("delete", "/api/appointment-types/{appointment_type_id}"),  # deletes an appointment type
        # what connecting can be limited to, generated from provider declarations
        ("get", "/api/google-calendar/consent-options"),
        ("get", "/api/google-calendar/authorize"),  # OAuth start, returns auth URL
        ("get", "/api/google-calendar/callback"),  # OAuth token exchange, no events
        ("get", "/api/google-calendar/status"),  # calendar connection status
        ("delete", "/api/google-calendar/disconnect"),  # removes calendar tokens
        # calendar_import.py — busy/free times only, no event content (scan/confirm
        # below DO carry event content and are audited, not exempted)
        ("get", "/api/calendar/import/busy"),  # freebusy start/end blocks, never titles
        # users.py — caller's OWN account/profile, not patient data
        ("get", "/api/users/baa"),  # current BAA document text
        ("get", "/api/users/baa/{version}"),  # versioned BAA document text
        ("get", "/api/users/me"),  # caller's own profile
        ("get", "/api/users/me/baa-status"),  # caller's own BAA status
        ("get", "/api/users/me/preferences"),  # caller's own UI preferences
        ("get", "/api/users/me/security-guide-status"),  # caller's own security-guide status
        ("get", "/api/users/me/status"),  # caller's own account/onboarding status
        ("get", "/api/users/me/devices"),  # caller's own enrolled companion installs, no PHI
        ("patch", "/api/users/me"),  # updates caller's own profile
        ("patch", "/api/users/me/professional-info"),  # updates caller's own license info
        ("post", "/api/users/me/accept-baa"),  # records caller's own BAA acceptance
        ("post", "/api/users/me/acknowledge-security-guide"),  # records caller's own ack
        ("post", "/api/users/me/mfa-enrolled"),  # records caller's own MFA enrollment
        ("put", "/api/users/me/preferences"),  # saves caller's own preferences
        ("put", "/api/users/me/preferences/theme"),  # saves caller's own theme
        # supervision.py — clinician's own oversight relationships + accrued hours (no patient PHI)
        ("get", "/api/supervision"),  # lists clinician's own supervision relationships
        ("post", "/api/supervision"),  # creates a clinician supervision relationship
        ("put", "/api/supervision/{relationship_id}"),  # updates a supervision relationship
        ("delete", "/api/supervision/{relationship_id}"),  # deletes a supervision relationship
        ("get", "/api/supervision/{relationship_id}/hours"),  # lists accrued hours (own records)
        ("post", "/api/supervision/{relationship_id}/hours"),  # logs an accrued-hours entry
    }
)


def _router_prefixes(tree: ast.Module) -> dict[str, str]:
    """Map each ``X = APIRouter(prefix="...")`` variable to its prefix.

    Route files use two styles: some put the full path in the decorator
    (``@router.get("/api/sessions")``), others use a router prefix plus a
    relative path (``APIRouter(prefix="/api/patients")`` + ``@router.get("")``).
    Resolving the prefix lets PHI-marker matching see the *mounted* path in
    both styles — without it, every prefixed router is invisible to the check.
    """
    prefixes: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        is_router_ctor = (isinstance(func, ast.Name) and func.id == "APIRouter") or (
            isinstance(func, ast.Attribute) and func.attr == "APIRouter"
        )
        if not is_router_ctor:
            continue
        prefix = ""
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                prefix = kw.value.value if isinstance(kw.value.value, str) else ""
        for target in node.targets:
            if isinstance(target, ast.Name):
                prefixes[target.id] = prefix
    return prefixes


def _iter_route_handlers(
    files: list[Path],
) -> list[tuple[str, str, ast.FunctionDef | ast.AsyncFunctionDef, Path]]:
    """Return (mounted_path, method, function_node, file) for every
    ``@router.<method>`` handler in ``files``."""
    handlers: list[tuple[str, str, ast.FunctionDef | ast.AsyncFunctionDef, Path]] = []
    for py_file in files:
        tree = ast.parse(py_file.read_text())
        prefixes = _router_prefixes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                    continue
                if not isinstance(dec.func.value, ast.Name):
                    continue
                router_name = dec.func.value.id
                # A decorator receiver counts as a router if it was assigned an
                # APIRouter in this file (robust to any variable name across the
                # SaaS overlay's many routers), with a name-convention fallback
                # for routers imported from elsewhere.
                if (
                    router_name not in prefixes
                    and router_name != "router"
                    and not router_name.endswith("_router")
                ):
                    continue
                if dec.func.attr not in HTTP_METHODS:
                    continue
                if not dec.args or not isinstance(dec.args[0], ast.Constant):
                    continue
                path = dec.args[0].value
                if not isinstance(path, str):
                    continue
                full_path = prefixes.get(router_name, "") + path
                handlers.append((full_path, dec.func.attr, node, py_file))
    return handlers


def _param_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    return [arg.arg for arg in (*func.args.args, *func.args.kwonlyargs)]


def _param_annotations(func: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    return {
        arg.arg: ast.unparse(arg.annotation)
        for arg in (*func.args.args, *func.args.kwonlyargs)
        if arg.annotation is not None
    }


def _injects_tenant_audit(annotations: dict[str, str]) -> bool:
    """True if any parameter is annotated with the *tenant* ``AuditService``.

    The platform sink (``PlatformAuditService``) contains ``AuditService`` as a
    substring but writes the PHI-free cross-tenant ops log, not the per-tenant
    HIPAA ``audit_logs`` — so it must NOT satisfy the PHI check. Match each
    annotation individually so a handler that injects both services still
    counts on its tenant-``AuditService`` parameter.
    """
    return any(
        "AuditService" in ann and "PlatformAuditService" not in ann for ann in annotations.values()
    )


def _calls_audit(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the function body contains any ``audit.<attr>(...)`` call."""
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "audit"
        ):
            return True
    return False


def _is_route_file(p: Path) -> bool:
    rp = p.resolve()
    if rp.suffix != ".py" or rp.name == "__init__.py" or "__pycache__" in rp.parts:
        return False
    return any(root in rp.parents for root in ROUTE_ROOTS)


def _route_files(paths: list[Path] | None) -> list[Path]:
    """Resolve which route files to scan. ``None`` → every ``*.py`` under the
    detected route roots. Otherwise keep only inputs that live under a route
    root (so a hook can pass an arbitrary edited file and we no-op on non-route
    edits)."""
    if paths is None:
        found: set[Path] = set()
        for root in ROUTE_ROOTS:
            found.update(
                p
                for p in root.rglob("*.py")
                if p.name != "__init__.py" and "__pycache__" not in p.parts
            )
        return sorted(found)
    return [p.resolve() for p in paths if _is_route_file(p)]


def underscore_param_violations(files: list[Path]) -> list[str]:
    out: list[str] = []
    for path, method, func, py_file in _iter_route_handlers(files):
        for name in _param_names(func):
            if name in FORBIDDEN_UNDERSCORE_PARAMS:
                out.append(
                    f"{py_file.name}::{func.name} ({method.upper()} {path}) "
                    f"declares forbidden parameter `{name}` (underscore silences linters)"
                )
    return out


def injected_but_uncalled_violations(files: list[Path]) -> list[str]:
    out: list[str] = []
    for path, method, func, py_file in _iter_route_handlers(files):
        annotations = _param_annotations(func)
        if not _injects_tenant_audit(annotations):
            continue
        if not _calls_audit(func):
            out.append(
                f"{py_file.name}::{func.name} ({method.upper()} {path}) "
                f"injects AuditService but never calls audit.*"
            )
    return out


def fail_closed_audit_violations(files: list[Path]) -> list[str]:
    """Every handler must audit, be a reviewed PHI-marker exemption, or be an
    explicit non-PHI classification. Anything else is a fail-closed violation."""
    out: list[str] = []
    for path, method, func, py_file in _iter_route_handlers(files):
        if _injects_tenant_audit(_param_annotations(func)):
            continue  # audits (injected-but-uncalled rule covers dead injection)
        marker = next((m for m in PHI_PATH_MARKERS if m in path), None)
        if marker is not None:
            # PHI-marker path: the reviewed marker list is the only escape.
            if (method, path) in AUDIT_EXEMPT_PHI_ROUTES:
                continue
            out.append(
                f"{py_file.name}::{func.name} ({method.upper()} {path}) "
                f"matches PHI marker '{marker}' but does not inject AuditService"
            )
            continue
        # No marker: fail-closed — must be explicitly classified non-PHI.
        if (method, path) in AUDIT_EXEMPT_NON_PHI_ROUTES:
            continue
        out.append(
            f"{py_file.name}::{func.name} ({method.upper()} {path}) "
            f"is unaudited and unclassified — inject the tenant AuditService, or "
            f"if it touches no patient PHI add (method, mounted_path) to "
            f"AUDIT_EXEMPT_NON_PHI_ROUTES with a reason"
        )
    return out


def _exempt_config_violations() -> list[str]:
    """Backstop: a PHI-marker path may never be lazily exempted. Any entry in
    AUDIT_EXEMPT_NON_PHI_ROUTES that matches a marker is a hard config error."""
    out: list[str] = []
    for method, path in sorted(AUDIT_EXEMPT_NON_PHI_ROUTES):
        marker = next((m for m in PHI_PATH_MARKERS if m in path), None)
        if marker is not None:
            out.append(
                f"AUDIT_EXEMPT_NON_PHI_ROUTES contains ({method!r}, {path!r}) which "
                f"matches PHI marker '{marker}' — a PHI-marker path may not be lazily "
                f"exempted. Audit it, or if it is genuinely metadata-only move it to "
                f"the reviewed AUDIT_EXEMPT_PHI_ROUTES list."
            )
    return out


def iter_route_paths(paths: list[Path] | None = None) -> list[tuple[str, str]]:
    """Return sorted (method, mounted_path) pairs for every route handler.

    Same AST walk ``find_violations`` uses, exposed for callers that just want
    the route table (e.g. the pentest context pack) without the audit checks.
    """
    files = _route_files(paths)
    return sorted({(method, path) for path, method, _func, _file in _iter_route_handlers(files)})


def find_violations(paths: list[Path] | None = None) -> list[str]:
    files = _route_files(paths)
    if not files:
        # Non-route edit (e.g. a hook firing on an unrelated file) → no-op. The
        # static config check still runs on any real scan, since a full scan or
        # a route-file edit always yields files.
        return []
    return [
        *_exempt_config_violations(),
        *underscore_param_violations(files),
        *injected_but_uncalled_violations(files),
        *fail_closed_audit_violations(files),
    ]


_FIX_HINT = (
    "Fix: if the route touches patient PHI, inject `audit: AuditService = "
    "Depends(get_audit_service)` and log the access (e.g. audit.log_*_action(...)). "
    "If it touches NO patient PHI, add (method, mounted_path) to "
    "AUDIT_EXEMPT_NON_PHI_ROUTES in backend/scripts/check_route_audit.py with a "
    "one-line reason. A PHI-marker path (e.g. /sessions, /patients) may only go in "
    "the reviewed AUDIT_EXEMPT_PHI_ROUTES list, never the non-PHI one. The platform "
    "PlatformAuditService does NOT satisfy this — PHI must hit the tenant AuditService. "
    "(CLAUDE.md guardrail #1 — PHI access without an audit entry is a HIPAA gap.)"
)


def main(argv: list[str] | None = None) -> int:
    paths = [Path(a) for a in argv] if argv else None
    violations = find_violations(paths)
    if not violations:
        return 0
    print("Route-audit guardrail failed:", file=sys.stderr)
    for v in violations:
        print(f"  - {v}", file=sys.stderr)
    print(f"\n{_FIX_HINT}", file=sys.stderr)
    return 1


def run_hook() -> int:
    """Claude Code PostToolUse entry point.

    Reads the hook payload from stdin, acts only when the edited file is a
    route module, and exits 2 (the code that feeds stderr back to the agent)
    if that file introduces a guardrail violation. Any malformed payload or
    non-route edit is a silent exit 0 — the hook must never get in the way of
    unrelated edits.
    """
    import json

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    file_path = (payload.get("tool_input") or {}).get("file_path")
    if not file_path:
        return 0
    violations = find_violations([Path(file_path)])
    if not violations:
        return 0
    print("Route-audit guardrail (edit-time):", file=sys.stderr)
    for v in violations:
        print(f"  - {v}", file=sys.stderr)
    print(f"\n{_FIX_HINT}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    if "--hook" in sys.argv[1:]:
        raise SystemExit(run_hook())
    raise SystemExit(main(sys.argv[1:]))
