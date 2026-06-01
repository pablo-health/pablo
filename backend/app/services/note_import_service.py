# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Parse an existing, already-written SOAP note into the structured shape.

Where :mod:`note_generation_service` *synthesizes* a note from a session
transcript, this module *extracts* the content of a note the clinician has
already written — e.g. a PDF or Word doc exported from another records
system — and maps it into the registry's SOAP fields without inventing any
clinical material. It also reads the date (and time, when present) the
session took place, so an imported note can be filed against the day it
actually happened rather than the day it was uploaded.

The extracted text feeds the same structured-output gateway and the same
SOAP response schema used for generation, so the resulting ``content`` is
shape-identical to a generated note and renders in the editor unchanged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from ..notes import NoteTypeDefinition, NoteTypeRegistry, get_default_registry
from ..settings import get_settings

# These helpers build/validate the registry-shaped JSON for a note type.
# They are imported (not reimplemented) so an imported note is exactly the
# same shape as a generated one; see CLAUDE.md "Don't duplicate OSS".
from .note_generation_service import (
    SOAP_KEY,
    _build_registry_response_schema,
    _coerce_registry_response,
)
from .structured_llm_gateway import (
    StructuredCompletion,
    StructuredLLMGateway,
    StructuredOutputTruncatedError,
    get_default_structured_llm_gateway,
)

logger = logging.getLogger(__name__)

# Mirror patient_documents_service: PyMuPDF text shorter than this almost
# always means a scanned / image-only PDF with no embedded text layer.
_SCANNED_PDF_TEXT_THRESHOLD = 100

# Keys we add to the SOAP response schema so the model also reports when the
# session occurred. Kept distinct from the note ``content`` so they never
# leak into the rendered note body.
_SESSION_DATE_KEY = "session_date"
_SESSION_TIME_KEY = "session_time"

EXTRACT_SYSTEM_PROMPT = (
    "You are a clinical documentation assistant. You are given the full text "
    "of an existing, already-written therapy progress note in SOAP format "
    "(often exported from another records system). Your job is to RELOCATE "
    "that note's existing text into the named fields below — not to rewrite "
    "it.\n\n"
    "Rules:\n"
    "- Quote the source text VERBATIM. Copy the clinician's exact words into "
    "each field. Do not rephrase, summarize, paraphrase, reorder words, "
    "normalize pronouns, or otherwise 'clean up' the wording — not even "
    "slightly.\n"
    "- Do NOT invent, infer, or add any content that is not present in the "
    "source text.\n"
    "- When the source files a detail under a heading we do not have, place "
    "it under the field whose meaning fits best, but keep the source's exact "
    "wording (including any sub-labels) intact.\n"
    "- If the source has no content for a field, return an empty string (or "
    "an empty list for list fields). Never fabricate text to fill a field."
)


class DocumentTextExtractionError(ValueError):
    """Raised when an uploaded document yields no usable text.

    The common case is a scanned / image-only PDF that has no embedded text
    layer; rather than import an empty note, callers should surface this so
    the clinician knows to upload a text-based export.
    """


class UnsupportedDocumentTypeError(ValueError):
    """Raised when an uploaded file is not a supported import format."""


@dataclass(frozen=True)
class ParsedImportedNote:
    """Result of parsing an uploaded SOAP note.

    ``content`` is the registry-shaped SOAP dict — identical in shape to a
    generated note's ``content`` — and renders in the note editor unchanged.
    ``session_date`` / ``session_time`` are read from the document, or
    ``None`` when the document did not state them (the time often is absent).
    """

    content: dict[str, Any]
    session_date: date | None
    session_time: time | None

    def session_datetime(self, *, default_time: time = time(0, 0)) -> datetime | None:
        """Combine the parsed date and time into a naive datetime.

        Returns ``None`` when no date was found. When the document stated a
        date but no time, ``default_time`` (midnight) is used so the caller
        still gets a usable ``session_date`` for the session row.
        """
        if self.session_date is None:
            return None
        return datetime.combine(self.session_date, self.session_time or default_time)


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------


def _looks_like_pdf(content_type: str | None, filename: str | None) -> bool:
    if content_type and "pdf" in content_type.lower():
        return True
    return bool(filename and filename.lower().endswith(".pdf"))


def _looks_like_text(content_type: str | None, filename: str | None) -> bool:
    if content_type and content_type.lower().startswith("text/"):
        return True
    return bool(filename and filename.lower().endswith(".txt"))


def _extract_pdf_text(data: bytes) -> str:
    """Pull the embedded text layer out of a PDF via PyMuPDF.

    Raises :class:`DocumentTextExtractionError` for a scanned / image-only
    PDF (text below :data:`_SCANNED_PDF_TEXT_THRESHOLD`).
    """
    import fitz  # type: ignore[import-untyped]  # PyMuPDF, imported lazily

    with fitz.open(stream=data, filetype="pdf") as doc:
        body = "".join(page.get_text() for page in doc).strip()
    if len(body) < _SCANNED_PDF_TEXT_THRESHOLD:
        raise DocumentTextExtractionError(
            "This PDF has no extractable text — it looks like a scan or image. "
            "Upload a text-based PDF or TXT export instead."
        )
    return body


def extract_document_text(
    data: bytes,
    *,
    content_type: str | None = None,
    filename: str | None = None,
) -> str:
    """Extract plain text from an uploaded clinical document.

    Supports text-based PDFs (PyMuPDF) and plain-text files. Word (.docx)
    support is tracked separately. Raises
    :class:`UnsupportedDocumentTypeError` for anything else and
    :class:`DocumentTextExtractionError` when a supported file yields no
    usable text.
    """
    if _looks_like_pdf(content_type, filename):
        return _extract_pdf_text(data)
    if _looks_like_text(content_type, filename):
        body = data.decode("utf-8", errors="replace").strip()
        if not body:
            raise DocumentTextExtractionError("The uploaded file is empty.")
        return body
    raise UnsupportedDocumentTypeError(
        f"Unsupported document type (content_type={content_type!r}, "
        f"filename={filename!r}). Supported formats: PDF, TXT."
    )


# ---------------------------------------------------------------------------
# Structured parse
# ---------------------------------------------------------------------------


def _build_extract_prompt(definition: NoteTypeDefinition, source_text: str) -> str:
    """Render the field guide + source note into the extraction user prompt."""
    lines: list[str] = []
    for section in definition.sections:
        lines.append(f"## {section.key} — {section.label}")
        for fld in section.fields:
            kind = "list of strings" if fld.kind == "list" else "text"
            hint = f" — {fld.ai_hint}" if fld.ai_hint else ""
            lines.append(f"- {fld.key} ({kind}): {fld.label}{hint}")
    field_guide = "\n".join(lines)

    return f"""# Source note
The following is the complete text of an existing SOAP note. Reorganize its
content into the fields described below.

\"\"\"
{source_text}
\"\"\"

# Output fields
{field_guide}

# Session date and time
Also report when *this* documented session took place:
- {_SESSION_DATE_KEY}: the session date as YYYY-MM-DD, or "" if not stated.
- {_SESSION_TIME_KEY}: the session time as HH:MM (24-hour), or "" if not stated.

Use the date/time of the session this note documents (usually labeled "Date"
or "Session Date" near the top). Do NOT use the date of the next appointment
or any future follow-up mentioned in the plan.
"""


def _build_extract_schema(definition: NoteTypeDefinition) -> dict[str, Any]:
    """SOAP registry schema plus the session date/time fields."""
    schema = _build_registry_response_schema(definition)
    schema["properties"][_SESSION_DATE_KEY] = {"type": "string"}
    schema["properties"][_SESSION_TIME_KEY] = {"type": "string"}
    return schema


def _parse_iso_date(raw: Any) -> date | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        logger.warning("Imported note had an unparseable session_date: %r", raw)
        return None


def _parse_iso_time(raw: Any) -> time | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        # Accept "HH:MM" and "HH:MM:SS".
        return time.fromisoformat(raw.strip())
    except ValueError:
        logger.warning("Imported note had an unparseable session_time: %r", raw)
        return None


class NoteImportService:
    """Parse an existing SOAP note's text into the structured note shape."""

    def __init__(
        self,
        llm_gateway: StructuredLLMGateway | None = None,
        registry: NoteTypeRegistry | None = None,
        model: str | None = None,
    ) -> None:
        self._llm_gateway = llm_gateway or get_default_structured_llm_gateway()
        self._registry = registry or get_default_registry()
        self._model = model

    def _resolve_model(self) -> str:
        return self._model or get_settings().ai_model

    def _complete_with_retry(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
    ) -> StructuredCompletion:
        """One structured call, retried once at 2x budget if truncated.

        Mirrors ``note_generation_service`` — thinking models can spend the
        output budget on reasoning and truncate the JSON tail on a long note.
        """
        base_budget = get_settings().note_max_output_tokens
        last_truncation: StructuredOutputTruncatedError | None = None
        for budget in (base_budget, base_budget * 2):
            try:
                return self._llm_gateway.complete_structured(
                    model=self._resolve_model(),
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_schema=response_schema,
                    max_output_tokens=budget,
                    # Verbatim relocation, not generation: keep it deterministic.
                    temperature=0.0,
                )
            except StructuredOutputTruncatedError as exc:
                last_truncation = exc
                logger.warning(
                    "Imported-note parse truncated at max_output_tokens=%d (%s)",
                    budget,
                    "retrying at 2x" if budget == base_budget else "giving up",
                )
                continue
            except Exception as exc:
                logger.exception("Imported-note parse failed")
                raise ValueError(f"Note import parse failed: {exc}") from exc
        raise ValueError(f"Note import parse failed: {last_truncation}") from last_truncation

    def parse_soap_note(self, source_text: str) -> ParsedImportedNote:
        """Parse the extracted text of a SOAP note into structured content."""
        definition = self._registry.get(SOAP_KEY)
        completion = self._complete_with_retry(
            system_prompt=EXTRACT_SYSTEM_PROMPT,
            user_prompt=_build_extract_prompt(definition, source_text),
            response_schema=_build_extract_schema(definition),
        )
        data = completion.data
        return ParsedImportedNote(
            content=_coerce_registry_response(definition, data),
            session_date=_parse_iso_date(data.get(_SESSION_DATE_KEY)),
            session_time=_parse_iso_time(data.get(_SESSION_TIME_KEY)),
        )
