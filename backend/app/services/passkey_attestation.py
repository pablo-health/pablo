# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""WebAuthn attestation provenance verification against curated trust roots.

The conveyance commit requests attestation and records the format/AAGUID;
this module turns that into a trust verdict. ``py_webauthn`` already does
the certificate-chain validation when handed root CAs via
``pem_root_certs_bytes_by_fmt`` — so the work here is loading a curated set
of vendor roots (one PEM bundle per attestation format) and asking the
library whether the credential's attestation chains to one of them.

The trust store is operator-provisioned: root-CA bytes are downloaded and
fingerprint-verified by a human, never embedded here. With no store
configured, ``attestation_verified`` is always false — credentials still
enrol, the verdict is simply "unverified". See PABLO-f00.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from webauthn import verify_registration_response
from webauthn.helpers import base64url_to_bytes, parse_attestation_object
from webauthn.helpers.structs import AttestationFormat

logger = logging.getLogger(__name__)


class AttestationUntrustedError(Exception):
    """Strict mode: a curated-format attestation failed to chain to a root."""


@lru_cache(maxsize=4)
def _load_roots(roots_dir: str) -> tuple[tuple[AttestationFormat, tuple[bytes, ...]], ...]:
    """Load per-format root PEM bundles from ``roots_dir`` (cached by path).

    Each ``<fmt>.pem`` may concatenate several PEM roots; the whole file's
    bytes are handed to the library, which splits the chain itself.
    Returns a hashable structure so ``lru_cache`` can memoise it.
    """
    base = Path(roots_dir)
    loaded: list[tuple[AttestationFormat, tuple[bytes, ...]]] = []
    if not base.is_dir():
        logger.warning("webauthn attestation roots dir not found: %s", roots_dir)
        return ()
    for fmt in AttestationFormat:
        pem = base / f"{fmt.value}.pem"
        if pem.is_file():
            loaded.append((fmt, (pem.read_bytes(),)))
    if not loaded:
        logger.warning("webauthn attestation roots dir has no <fmt>.pem files: %s", roots_dir)
    return tuple(loaded)


class AttestationVerifier:
    """Decides whether a registration's attestation chains to a curated root."""

    def __init__(self, roots: dict[AttestationFormat, list[bytes]]) -> None:
        self._roots = roots

    @property
    def configured(self) -> bool:
        return bool(self._roots)

    def evaluate(
        self,
        *,
        credential: str,
        expected_challenge: bytes,
        expected_origin: list[str] | str,
        expected_rp_id: str,
        strict: bool,
    ) -> bool:
        """Return whether the attestation is trusted (chains to a curated root).

        Self-attestation, ``none`` attestation, an unknown format, or a
        format we hold no roots for all yield ``False`` (no provenance to
        trust) without rejecting the enrolment. When ``strict`` is set and
        the format IS one we curate but its certificate chain does not
        validate, raises ``AttestationUntrustedError``.
        """
        if not self._roots:
            return False

        fmt, has_x5c = self._parse(credential)
        if fmt is None or not has_x5c:
            # Synced passkeys (fmt 'none') and self-attestation carry no chain
            # to verify — they are device-bound/trusted by other signals, not here.
            return False
        roots = self._roots.get(fmt)
        if roots is None:
            return False

        try:
            verify_registration_response(
                credential=credential,
                expected_challenge=expected_challenge,
                expected_origin=expected_origin,
                expected_rp_id=expected_rp_id,
                require_user_verification=True,
                pem_root_certs_bytes_by_fmt={fmt: roots},
            )
        except Exception as err:
            if strict:
                raise AttestationUntrustedError(fmt.value) from err
            logger.info("passkey_attestation_untrusted fmt=%s reason=%s", fmt.value, err)
            return False
        return True

    @staticmethod
    def _parse(credential: str) -> tuple[AttestationFormat | None, bool]:
        """Extract the attestation format and whether it carries an x5c chain."""
        import json

        try:
            att_obj_b64 = json.loads(credential)["response"]["attestationObject"]
            att = parse_attestation_object(base64url_to_bytes(att_obj_b64))
        except (KeyError, TypeError, ValueError):
            return None, False
        return att.fmt, bool(att.att_stmt.x5c)


def build_attestation_verifier(roots_dir: str) -> AttestationVerifier:
    """Construct a verifier from the configured roots directory (empty = inert)."""
    if not roots_dir:
        return AttestationVerifier({})
    roots = {fmt: list(bundle) for fmt, bundle in _load_roots(roots_dir)}
    return AttestationVerifier(roots)
