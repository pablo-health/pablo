# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The portal invitation email, as a practice writes it.

A practice can word its own invitation: a subject, a plain-text body, and a
handful of named placeholders filled in at send time. This module is the one
place that knows the placeholders, checks a template, and renders it — for the
preview a clinician looks at AND for the email that goes out, so the two cannot
differ.

**Plain text only.** No markup and no formatting to escape: what a clinician
types is what the client reads, in every mail client.

**The link is required, and only in the body.** An invitation without it is not
an invitation, and a subject line is shown in notification previews on a lock
screen, which is no place for a credential.

**The default says who it is from.** An invitation from nobody reads like
spam, and a client is far more likely to open one from their own clinician. So
the default names the client's clinician and the practice. It does not greet
the client by name: a practice that wants a greeting adds one with
``{{client_first_name}}``.

``{{clinician_name}}`` is the client's primary clinician on the chart, not
whoever pressed Send, since front-desk staff send invitations too. Where there
is no primary clinician, or no name on file for them, the practice's name
stands in, so an email never carries an empty name.

Whether a deployment's email channel can send practice-written text at all is
the adapter's to say — see :class:`app.portal.delivery.RenderedInviteDelivery`.
Nothing here sends anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Placeholder name -> what a clinician sees it called.
PLACEHOLDERS: dict[str, str] = {
    "portal_link": "Sign-in link",
    "client_first_name": "Client's first name",
    #: Labelled for the clinician reading the editor, whose name it usually is.
    "clinician_name": "Your name",
    "practice_name": "Practice name",
    "forms": "Forms to fill in",
    "link_expiry": "How long the link works",
}

REQUIRED_PLACEHOLDER = "portal_link"

MAX_SUBJECT_LENGTH = 200
MAX_BODY_LENGTH = 5000

DEFAULT_SUBJECT = "{{clinician_name}} invited you to your patient portal"
DEFAULT_BODY = (
    "{{clinician_name}} has invited you to the patient portal for {{practice_name}}.\n\n"
    "Use this link to sign in:\n\n"
    "{{portal_link}}\n\n"
    "When you open the link, we'll text a code to your phone. "
    "The link works for {{link_expiry}}."
)

#: Stands in for the link wherever a template is rendered to be looked at
#: rather than sent. The real link is a credential and is never rendered
#: anywhere a clinician can read it.
PREVIEW_LINK = "[personal sign-in link]"

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


@dataclass(frozen=True)
class InviteTemplate:
    subject: str
    body: str


DEFAULT_TEMPLATE = InviteTemplate(subject=DEFAULT_SUBJECT, body=DEFAULT_BODY)


@dataclass(frozen=True)
class InviteContext:
    """Everything a template can refer to, for one send."""

    portal_link: str
    client_first_name: str
    practice_name: str
    forms: list[str]
    link_expiry: str
    #: Already resolved, fallback included — see the module docstring.
    clinician_name: str = ""


@dataclass(frozen=True)
class RenderedInvite:
    subject: str
    text: str


def template_problems(template: InviteTemplate) -> list[str]:
    """What stops this template being saved, in words a clinician can act on.

    Empty when it is fine.
    """
    problems: list[str] = []
    subject = template.subject.strip()
    body = template.body.strip()
    if not subject:
        problems.append("Add a subject.")
    elif len(subject) > MAX_SUBJECT_LENGTH:
        problems.append(f"Keep the subject under {MAX_SUBJECT_LENGTH} characters.")
    if "\n" in subject:
        problems.append("Keep the subject on one line.")
    if len(body) > MAX_BODY_LENGTH:
        problems.append(f"Keep the message under {MAX_BODY_LENGTH} characters.")

    unknown = sorted({name for name in _names(subject) + _names(body) if name not in PLACEHOLDERS})
    if unknown:
        problems.append(
            "Remove the placeholders this email cannot fill in: "
            + ", ".join(f"{{{{{name}}}}}" for name in unknown)
            + "."
        )
    if REQUIRED_PLACEHOLDER in _names(subject):
        problems.append("Put the sign-in link in the message, not the subject.")
    if REQUIRED_PLACEHOLDER not in _names(body):
        problems.append("Include {{portal_link}} in the message, so the client can sign in.")
    return problems


def render(template: InviteTemplate, context: InviteContext) -> RenderedInvite:
    """Fill a template in. The template is assumed to have passed
    :func:`template_problems`; an unknown placeholder is left as typed."""
    values = {
        "portal_link": context.portal_link,
        "client_first_name": context.client_first_name,
        "clinician_name": context.clinician_name,
        "practice_name": context.practice_name,
        "forms": _form_list(context.forms),
        "link_expiry": context.link_expiry,
    }

    def fill(text: str) -> str:
        return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), text)

    return RenderedInvite(subject=fill(template.subject.strip()), text=fill(template.body.strip()))


def describe_duration(seconds: int) -> str:
    """``900`` -> ``15 minutes``: the link lifetime as a client reads it."""
    minutes = max(1, round(seconds / 60))
    if minutes % 1440 == 0:
        days = minutes // 1440
        return "1 day" if days == 1 else f"{days} days"
    if minutes % 60 == 0:
        hours = minutes // 60
        return "1 hour" if hours == 1 else f"{hours} hours"
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


def _names(text: str) -> list[str]:
    return _PLACEHOLDER_RE.findall(text)


def _form_list(forms: list[str]) -> str:
    """One form per line, and nothing at all when none is outstanding.

    A list rather than a comma-joined line so a long form name cannot run
    into the next one. Empty rather than a stand-in phrase: an invitation
    sent with no form waiting would otherwise tell the client about one.
    """
    return "\n".join(f"- {name}" for name in forms)
