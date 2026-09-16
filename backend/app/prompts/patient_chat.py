# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""System prompt for the patient-principal chat surface.

Sibling of :mod:`app.prompts.chat`, and deliberately a separate module
rather than a ``provider_type`` branch inside it. The clinician prompt's
whole job is to ground answers in a chart; this one's job is to stay off
clinical ground entirely. Sharing a module would invite a future edit to
the shared half that quietly relaxes one of the two.

**This prompt is a floor, not a personality.** Two properties are baked
into the text because the surface ships before the machinery that would
otherwise enforce them:

1. **No medical advice.** The patient-facing assistant does not diagnose,
   does not interpret symptoms, does not advise on medication, and does
   not answer "is this normal?" — it says what it cannot do and points
   back to the practice.
2. **Crisis is never answered casually.** On any signal of self-harm,
   harm to others, or acute crisis, the reply does not counsel, does not
   assess severity, and does not attempt to talk the person down. It
   surfaces immediate human help.

Neither property may be removed by a downstream prompt. A registered
resolver *replaces* this text, so a downstream consumer that registers
one owns re-stating both floors — :func:`get_patient_chat_system_prompt`
is the seam, and the test suite pins the default's floors, not a
registered replacement's.

**This is a temporary default.** The durable design routes patient-facing
content through therapist-authored material, with the model doing
sequencing and tone rather than clinical substance. When that lands, the
envelope it composes supersedes this text via :func:`register_resolver`.
Nothing here is a clinical-generation surface, and nothing should be
added to it that becomes one.

Carrying the floors in the prompt is also not the same as enforcing them.
Exposure to a real patient gates on the always-on crisis floor shipping
independently of this text; see the deployment notes on
``settings.enable_patient_chat``.
"""

from __future__ import annotations

from collections.abc import Callable

type PatientPromptResolver = Callable[[], str]


DEFAULT_PATIENT_PROMPT: str = """\
You are Pablo, a between-visits assistant for someone who is seeing a
mental-health clinician. You are not that clinician, and you are not a
clinician at all. Say so plainly whenever it matters.

WHAT YOU DO
Keep company between appointments. Listen, reflect back what you hear,
and help the person put words to how their week has gone so they can
bring it to their next session. Keep replies short and plain.

WHAT YOU NEVER DO
Never diagnose, never name a condition someone might have, and never
interpret symptoms. Never give medical, medication, or dosage advice —
including whether to start, stop, change, or skip anything. Never
comment on whether a symptom, feeling, or side effect is normal,
expected, or concerning. Never suggest a treatment, exercise, or
technique as something that will help a condition.

When a question needs any of that, say you are not able to answer it and
that their care team is the right place to take it. Offer to help them
write the question down for their next appointment. Do not soften the
refusal by guessing anyway, and do not add a general-information version
of the answer.

IF SOMEONE IS IN CRISIS
If there is any sign of thoughts of suicide or self-harm, of harming
someone else, of abuse, or of a medical emergency: stop the ordinary
conversation. Do not assess how serious it is, do not ask screening
questions, do not offer coping techniques, and do not try to persuade
anyone of anything. Say clearly that you are not able to help with this
directly, that help is available right now from a person, and direct
them to emergency services or a crisis line in their area. Stay warm and
stay brief.

HOW YOU SPEAK
Second person. Plain words. No clinical vocabulary. Do not refer to the
person's clinician by a gendered pronoun — say "your clinician" or
"they". Do not claim to know anything about the person's chart, notes,
appointments, or history: you cannot see any of it, so never imply
otherwise.
"""


# Registry slot for the therapist-authored content layer to supply the
# real envelope once it exists. Mirrors ``app.prompts.chat`` so both
# prompt surfaces have the same override story.
_resolver: PatientPromptResolver | None = None


def get_patient_chat_system_prompt() -> str:
    """Resolve the system prompt for a patient-principal conversation.

    Returns the registered resolver's text when a downstream consumer has
    supplied one, else :data:`DEFAULT_PATIENT_PROMPT`.

    No ``provider_type`` argument, deliberately: the clinician prompt
    varies by the provider it is assisting, whereas this surface answers
    to the patient in front of it and the floors do not vary by practice.
    """
    if _resolver is not None:
        return _resolver()
    return DEFAULT_PATIENT_PROMPT


def register_resolver(resolver: PatientPromptResolver) -> None:
    """Register a downstream resolver for the patient prompt.

    Idempotent; the intended call site is a downstream bootstrap before
    any request is served. The resolver **replaces** the default text, so
    whatever it returns must carry the medical-advice and crisis floors
    itself.
    """
    global _resolver  # noqa: PLW0603
    _resolver = resolver


def reset_resolver() -> None:
    """Clear any registered resolver. For tests; do not call from prod."""
    global _resolver  # noqa: PLW0603
    _resolver = None


__all__ = [
    "DEFAULT_PATIENT_PROMPT",
    "PatientPromptResolver",
    "get_patient_chat_system_prompt",
    "register_resolver",
    "reset_resolver",
]
