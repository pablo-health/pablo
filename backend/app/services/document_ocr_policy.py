# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Per-practice policy: may a scanned PDF fall back to OCR?

Finalizing an uploaded PDF reads its text layer with PyMuPDF. A scanned PDF
has none, so extraction falls back to Document AI OCR, a per-call cost. A
deployment may decline that fallback for some practices: the document is
still stored, viewable and downloadable, and a PDF with a text layer still
gets its text. Only the OCR call is skipped, and the document lands the way a
scan does when OCR is not configured at all (``extracted_via`` unset, no
text).

Same shape as ``register_clearinghouse_credential_provider`` in
``claims/credentials.py``: a module-level registration made once at
startup, with a default that offers OCR everywhere, so an install that
registers nothing behaves exactly as before.

The policy is asked only when a scanned PDF actually needs the fallback, so a
document with a text layer costs no extra lookup.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class DocumentOcrPolicy(Protocol):
    """Decides, per practice, whether a scanned PDF may be OCR'd."""

    def offers_ocr(self, tenant_schema: str | None) -> bool:
        """Whether the practice whose schema is ``tenant_schema`` gets OCR.

        ``tenant_schema`` is ``None`` when the caller did not resolve one.
        """
        ...


class OfferOcrEverywhere:
    """Default policy: every practice gets the OCR fallback."""

    def offers_ocr(
        self,
        tenant_schema: str | None,  # noqa: ARG002 — argument documents the protocol's shape
    ) -> bool:
        return True


@dataclass
class _PolicyRegistry:
    policy: DocumentOcrPolicy | None = None


_registry = _PolicyRegistry()
_default_policy = OfferOcrEverywhere()


def register_document_ocr_policy(policy: DocumentOcrPolicy | None) -> None:
    """Install the process-global policy, or pass ``None`` to restore the default.

    Call once during startup, before the first request.
    """
    _registry.policy = policy


def get_document_ocr_policy() -> DocumentOcrPolicy:
    """The registered policy, or :class:`OfferOcrEverywhere`."""
    return _registry.policy or _default_policy
