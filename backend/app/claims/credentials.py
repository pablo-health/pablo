# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where a practice's clearinghouse credentials come from.

Filing a claim or checking eligibility needs one thing: an API key for the
practice's own clearinghouse account. That is deployment configuration, so it
is read through a small provider rather than baked into the adapter, the same
way :mod:`app.payments.provider` resolves the Stripe secret key a practice
charges cards with.

``mode`` is not a separate setting a deployment has to keep in sync — it is
read off the key itself (a test key is answered by the vendor's test
environment, a production key by the live one), so there is no way for the
mode and the key to disagree.

:class:`SettingsClearinghouseCredentialProvider` is the default and is what a
bare deployment gets: the key configured as ``CLEARINGHOUSE_API_KEY``. A
deployment that needs something else — credentials fetched from a secret
store per practice, a key that rotates on its own schedule — implements the
protocol and installs it at startup with
:func:`register_clearinghouse_credential_provider`.

The registry is the same shape the rest of the codebase uses for this kind of
configuration point (see ``app.payments.provider`` and
``app.notes.registry``): a protocol, one implementation shipped here, and a
process-global setter called once during startup rather than per request.
Registration is a statement about the deployment, not about a request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from ..settings import get_settings

ClearinghouseMode = Literal["test", "production"]


@dataclass(frozen=True, slots=True)
class ClearinghouseCredentials:
    """What one clearinghouse call needs to be made for a practice.

    ``api_key`` authenticates the call, sent as the ``Authorization`` header.
    It is kept out of the dataclass ``repr`` so a logged or traceback-printed
    credentials object never carries the secret.

    ``mode`` is inferred from the key by whoever resolves it, not chosen
    separately — the vendor's test keys are answered by its test environment
    and never touch a real payer, so there is no separate "test mode" flag to
    forget to flip back.
    """

    api_key: str = field(repr=False)
    mode: ClearinghouseMode


class ClearinghouseCredentialProvider(Protocol):
    """Resolves a practice to the credentials its clearinghouse calls are made with."""

    def get(self, practice_id: str | None) -> ClearinghouseCredentials | None:
        """Return the credentials for ``practice_id``, or ``None``.

        ``None`` means this practice cannot file claims or check eligibility
        right now — nothing is configured, or setup is unfinished. Callers
        turn that into "not available", never into an error path that implies
        the request itself was wrong.

        ``practice_id`` is ``None`` on a deployment that runs a single
        practice and therefore has no practice registry to key on.
        """
        ...


class SettingsClearinghouseCredentialProvider:
    """Default provider: this deployment's own configured clearinghouse API key.

    ``practice_id`` is accepted and ignored: one deployment, one key, and
    reading it per call rather than at import time means a redeployed key
    takes effect without a code change.
    """

    def get(
        self,
        practice_id: str | None,  # noqa: ARG002 — argument documents the protocol's shape
    ) -> ClearinghouseCredentials | None:
        settings = get_settings()
        api_key = settings.clearinghouse_api_key
        if not api_key:
            return None
        return ClearinghouseCredentials(api_key=api_key, mode=mode_for_key(api_key))


#: The vendor's test API keys carry this prefix; production keys do not.
#: Confirmed against a real test key from the vendor's dashboard (the live
#: suite under ``tests_integration/clearinghouse_live`` refuses to run unless
#: the key it is handed classifies as ``test`` here). This is the only signal
#: the deployment ever needs to check — there is deliberately no separate
#: "which environment" setting to keep in sync with the key itself. If the
#: vendor changes its key format this is the one place to update.
_TEST_KEY_PREFIX = "test_"


def mode_for_key(api_key: str) -> ClearinghouseMode:
    """Which vendor environment answers ``api_key``: inferred from the key's prefix."""
    return "test" if api_key.startswith(_TEST_KEY_PREFIX) else "production"


@dataclass
class _ProviderRegistry:
    provider: ClearinghouseCredentialProvider | None = None


_registry = _ProviderRegistry()
_default_provider = SettingsClearinghouseCredentialProvider()


def register_clearinghouse_credential_provider(
    provider: ClearinghouseCredentialProvider | None,
) -> None:
    """Install the process-global provider, or pass ``None`` to restore the default.

    Call once during startup, before the first request. Tests use the
    ``None`` form to put the default back.
    """
    _registry.provider = provider


def get_clearinghouse_credential_provider() -> ClearinghouseCredentialProvider:
    """The registered provider, or :class:`SettingsClearinghouseCredentialProvider`."""
    return _registry.provider or _default_provider
