# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where a practice's clearinghouse credentials come from.

Filing a claim or checking eligibility needs an API key for the practice's
clearinghouse account. It is deployment configuration, read through a small
provider rather than baked into the adapter, the same way
:mod:`app.payments.provider` resolves the Stripe key.

``mode`` is read off the key itself rather than kept as a separate setting, so
the mode and the key cannot disagree.

:class:`SettingsClearinghouseCredentialProvider` is the default:
``CLEARINGHOUSE_API_KEY`` plus the optional ``CLEARINGHOUSE_BASE_URL`` naming
which server answers. A deployment that needs per-practice credentials or its
own rotation implements the protocol and installs it at startup with
:func:`register_clearinghouse_credential_provider`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from ..settings import get_settings

ClearinghouseMode = Literal["test", "production"]


@dataclass(frozen=True, slots=True)
class ClearinghouseCredentials:
    """What one clearinghouse call needs for a practice.

    ``api_key`` is kept out of ``repr`` so a logged or traceback-printed object
    never carries the secret. ``mode`` is inferred from the key. ``base_url``
    is the origin that answers, ``None`` for every deployment but a harness
    pointed at a stand-in; it rides here because "which account" and "which
    server" are one fact.
    """

    api_key: str = field(repr=False)
    mode: ClearinghouseMode
    base_url: str | None = None


class ClearinghouseCredentialProvider(Protocol):
    """Resolves a practice to the credentials its clearinghouse calls are made with."""

    def get(self, practice_id: str | None) -> ClearinghouseCredentials | None:
        """The credentials for ``practice_id``, or ``None``.

        ``None`` means claims are unavailable right now; callers turn it into
        "not available", never an error implying the request was wrong.
        ``practice_id`` is ``None`` on a deployment with no practice registry.
        """
        ...


class SettingsClearinghouseCredentialProvider:
    """Default provider: the deployment's own configured key.

    Read per call so a redeployed key needs no code change.
    """

    def get(
        self,
        practice_id: str | None,  # noqa: ARG002 — argument documents the protocol's shape
    ) -> ClearinghouseCredentials | None:
        settings = get_settings()
        api_key = settings.clearinghouse_api_key
        if not api_key:
            return None
        return ClearinghouseCredentials(
            api_key=api_key,
            mode=mode_for_key(api_key),
            base_url=settings.clearinghouse_base_url or None,
        )


#: The vendor's test keys are ``test_``-prefixed; production keys carry no
#: prefix (confirmed against a real test key on 2026-09-06, and the live suite
#: refuses to run unless its key classifies as ``test`` here). The one place
#: to update if the vendor changes its key format.
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

    Call once during startup, before the first request.
    """
    _registry.provider = provider


def get_clearinghouse_credential_provider() -> ClearinghouseCredentialProvider:
    """The registered provider, or :class:`SettingsClearinghouseCredentialProvider`."""
    return _registry.provider or _default_provider
