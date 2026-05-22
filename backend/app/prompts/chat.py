# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Chat system prompt — single source of truth for OSS + the SaaS overlay hook.

The prompt body lives here (not in frontend code, not duplicated in YAML
eval cases) so:

- Production and the Braintrust eval harness send the model the same prompt
  by construction; drift is impossible.
- Prompt iteration is a backend deploy, not a frontend rebuild.
- A downstream consumer (e.g. ``pablo-saas``) can register a provider-aware,
  proprietary prompt via :func:`register_provider` during its bootstrap
  without forking the OSS module.
- The frontend never sees the prompt text — it requests a conversation
  from the backend and the backend resolves the right prompt server-side
  based on the authenticated user's ``provider_type``.

The OSS default is a baseline-safety prompt. It explicitly tells the model:

1. Use only the provided chart context (don't draw on general clinical
   knowledge to fill gaps).
2. If the chart is empty for the requested sections, say so explicitly —
   do not describe a generic patient. (This is the safety floor for
   pablo-saas THERAPY-fr6y.)
3. Cite specific chart sources in bracketed names so a citation-manifest
   verifier can audit each claim.

See ``backend/evals/datasets/chat.yaml::chat-hallu-004`` and ``chat-hallu-005``
for the regression cases that validate this prompt.
"""

from __future__ import annotations

from collections.abc import Callable

type ProviderResolver = Callable[[str | None], str]


DEFAULT_PROMPT: str = """\
You are Pablo, a chart-aware assistant for a licensed clinician.

Answer ONLY from the patient context block below. Never infer,
extrapolate, or invent patient details — including demographics,
diagnoses, medications, history, or session content.

If the chart contains no information relevant to a question, say so
explicitly. If the entire chart is empty for the requested sections,
state that the chart contains no data and ask the clinician what
they would like to know about, rather than describing a generic
patient.

Cite which chart sources support each claim using bracketed names:
[intake], [progress notes], [treatment plan], [safety plan],
[medications]. If you cannot cite, do not state.
"""


# Internal registry slot for the SaaS overlay (or any other downstream
# consumer) to plug in a provider-aware resolver. The OSS path is
# deliberately model-neutral and provider-neutral — see ``chat_model_resolver``
# for the matching pattern on the model side.
_provider_resolver: ProviderResolver | None = None


def get_chat_system_prompt(provider_type: str | None = None) -> str:
    """Resolve the chat system prompt for a request.

    If a downstream consumer has registered a resolver via
    :func:`register_provider`, the registered resolver is invoked with the
    given ``provider_type``. Otherwise the OSS :data:`DEFAULT_PROMPT` is
    returned regardless of ``provider_type`` (OSS does not ship
    per-provider variants).
    """
    if _provider_resolver is not None:
        return _provider_resolver(provider_type)
    return DEFAULT_PROMPT


def register_provider(resolver: ProviderResolver) -> None:
    """Register a downstream resolver that returns a system prompt for a
    given ``provider_type``.

    Idempotent: calling this multiple times replaces the previously
    registered resolver. The intended call site is ``saas.bootstrap``
    (or equivalent) during application startup, before any chat
    requests are served.
    """
    global _provider_resolver  # noqa: PLW0603
    _provider_resolver = resolver


def reset_provider() -> None:
    """Clear any registered resolver. For tests; do not call from prod."""
    global _provider_resolver  # noqa: PLW0603
    _provider_resolver = None


__all__ = [
    "DEFAULT_PROMPT",
    "ProviderResolver",
    "get_chat_system_prompt",
    "register_provider",
    "reset_provider",
]
