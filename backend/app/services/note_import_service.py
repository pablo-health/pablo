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
import re
from dataclasses import dataclass, field
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
    _coerce_content_to_soap_note,
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

# Cap the total *uncompressed* size of a .docx so a small "zip bomb" can't
# expand to gigabytes in memory when python-docx reads the package.
_MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024

# Cap the extracted text fed to the parser. A single SOAP note is a few KB;
# anything past this is either not one note or an attempt to run up LLM cost.
_MAX_EXTRACTED_CHARS = 1_000_000

# Keys we add to the SOAP response schema so the model also reports when the
# session occurred. Kept distinct from the note ``content`` so they never
# leak into the rendered note body.
_SESSION_DATE_KEY = "session_date"
_SESSION_TIME_KEY = "session_time"

# A parsed field counts as "grounded" in the source when it is a
# whitespace-normalized substring of the source, or its word-token overlap
# with the source is at least this high (the model sometimes joins several
# verbatim source passages into one field, which breaks contiguous-substring
# matching but keeps every word). Below this, the field is flagged for review.
GROUNDING_OVERLAP_THRESHOLD = 0.9

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
class FieldGrounding:
    """Whether one parsed field's text is grounded in the source document.

    ``path`` is the field location (e.g. ``subjective.client_narrative`` or
    ``plan.homework_assignments[1]`` for a list item). ``overlap`` is the
    fraction of the field's word tokens that also appear in the source.
    ``grounded`` is the verdict (verbatim substring or high overlap).
    """

    path: str
    grounded: bool
    overlap: float


@dataclass(frozen=True)
class ParsedImportedNote:
    """Result of parsing an uploaded SOAP note.

    ``content`` is the registry-shaped SOAP dict — identical in shape to a
    generated note's ``content`` — and renders in the note editor unchanged.
    ``session_date`` / ``session_time`` are read from the document, or
    ``None`` when the document did not state them (the time often is absent).
    ``grounding`` reports, per field, whether the parse stayed faithful to
    the source text (see :func:`check_grounding`).
    """

    content: dict[str, Any]
    session_date: date | None
    session_time: time | None
    grounding: tuple[FieldGrounding, ...] = field(default_factory=tuple)

    @property
    def ungrounded(self) -> tuple[FieldGrounding, ...]:
        """Fields whose text was not found in the source — flag for review."""
        return tuple(g for g in self.grounding if not g.grounded)

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


def _looks_like_docx(content_type: str | None, filename: str | None) -> bool:
    if content_type and "wordprocessingml" in content_type.lower():
        return True
    return bool(filename and filename.lower().endswith(".docx"))


def _docx_block_lines(document: Any) -> list[str]:
    """Yield the document body's text in order — paragraphs and table cells.

    python-docx exposes paragraphs and tables separately; iterating the body
    element preserves their document order (so a leading metadata table reads
    before the narrative).
    """
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    lines: list[str] = []
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            text = Paragraph(child, document).text
            if text.strip():
                lines.append(text)
        elif isinstance(child, CT_Tbl):
            for row in Table(child, document).rows:
                for cell in row.cells:
                    if cell.text.strip():
                        lines.append(cell.text)
    return lines


def _extract_docx_text(data: bytes) -> str:
    """Extract a .docx's text with python-docx.

    Reads page headers/footers (clinical templates often put the date there),
    body paragraphs, and table cells in document order. python-docx's parser
    does not resolve XML entities, so entity-expansion ("billion laughs") /
    XXE don't apply; the uncompressed-size guard below covers zip bombs.
    """
    import io
    import zipfile

    import docx

    # Zip-bomb guard: reject if the package's declared uncompressed size
    # exceeds the cap before python-docx reads the whole thing into memory.
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            total_uncompressed = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile as exc:
        raise DocumentTextExtractionError(
            "This doesn't look like a readable Word (.docx) document."
        ) from exc
    if "word/document.xml" not in names:
        raise DocumentTextExtractionError(
            "This doesn't look like a readable Word (.docx) document."
        )
    if total_uncompressed > _MAX_DOCX_UNCOMPRESSED_BYTES:
        raise DocumentTextExtractionError("This Word document is too large to import.")

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise DocumentTextExtractionError(
            "This Word document's contents could not be read."
        ) from exc

    lines: list[str] = []
    for section in document.sections:
        for container in (section.header, section.footer):
            lines.extend(p.text for p in container.paragraphs if p.text.strip())
    lines.extend(_docx_block_lines(document))

    body = "\n".join(lines).strip()
    if len(body) < _SCANNED_PDF_TEXT_THRESHOLD:
        raise DocumentTextExtractionError(
            "This Word document has no extractable text. Upload a text-based export instead."
        )
    return body


def _extract_pdf_text(data: bytes) -> str:
    """Pull the embedded text layer out of a PDF via PyMuPDF.

    Raises :class:`DocumentTextExtractionError` for a scanned / image-only
    PDF (text below :data:`_SCANNED_PDF_TEXT_THRESHOLD`).
    """
    import fitz  # type: ignore[import-untyped]  # PyMuPDF, imported lazily

    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            body = "".join(page.get_text() for page in doc).strip()
    except Exception as exc:
        # MuPDF raises a range of errors on corrupt / encrypted / non-PDF
        # input; surface a clean 4xx rather than a 500 with a traceback.
        raise DocumentTextExtractionError(
            "Couldn't read this PDF — it may be corrupted, password-protected, or not a PDF."
        ) from exc
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

    Supports text-based PDFs (PyMuPDF), Word .docx (standard-library zip/XML),
    and plain-text files. Raises :class:`UnsupportedDocumentTypeError` for
    anything else and :class:`DocumentTextExtractionError` when a supported
    file yields no usable text.
    """
    if _looks_like_pdf(content_type, filename):
        text = _extract_pdf_text(data)
    elif _looks_like_docx(content_type, filename):
        text = _extract_docx_text(data)
    elif _looks_like_text(content_type, filename):
        text = data.decode("utf-8", errors="replace").strip()
        if not text:
            raise DocumentTextExtractionError("The uploaded file is empty.")
    else:
        raise UnsupportedDocumentTypeError(
            f"Unsupported document type (content_type={content_type!r}, "
            f"filename={filename!r}). Supported formats: PDF, Word (.docx), TXT."
        )

    if len(text) > _MAX_EXTRACTED_CHARS:
        raise DocumentTextExtractionError("This document is too long to import as a single note.")
    return text


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
        logger.warning("Imported note had an unparseable session_date")
        return None


def _parse_iso_time(raw: Any) -> time | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        # Accept "HH:MM" and "HH:MM:SS".
        return time.fromisoformat(raw.strip())
    except ValueError:
        logger.warning("Imported note had an unparseable session_time")
        return None


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _word_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def check_grounding(content: dict[str, Any], source_text: str) -> tuple[FieldGrounding, ...]:
    """Check each parsed field's text against the source — no LLM call.

    A field is grounded when its whitespace-normalized text is a substring of
    the source, or its word-token overlap with the source is at least
    :data:`GROUNDING_OVERLAP_THRESHOLD`. This catches paraphrase and
    fabrication deterministically: text the model invented or reworded will
    not be found in the source. List fields are checked per item.
    """
    normalized_source = _norm_ws(source_text)
    source_tokens = set(_word_tokens(source_text))

    results: list[FieldGrounding] = []
    for section, fields in content.items():
        if not isinstance(fields, dict):
            continue
        for key, value in fields.items():
            is_list = isinstance(value, list)
            items = value if is_list else ([value] if value else [])
            for index, item in enumerate(items):
                text = str(item).strip()
                if not text:
                    continue
                path = f"{section}.{key}" + (f"[{index}]" if is_list else "")
                tokens = _word_tokens(text)
                overlap = (
                    1.0 if not tokens else sum(t in source_tokens for t in tokens) / len(tokens)
                )
                grounded = (
                    _norm_ws(text) in normalized_source or overlap >= GROUNDING_OVERLAP_THRESHOLD
                )
                results.append(FieldGrounding(path=path, grounded=grounded, overlap=overlap))
    return tuple(results)


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
        # Import is verbatim relocation, not generation: a flash-tier model
        # with thinking disabled (see _complete_with_retry) is sufficient and
        # far faster than the pro/thinking default. Falls back to ai_model
        # when no flash model is configured.
        settings = get_settings()
        return self._model or settings.ai_model_flash or settings.ai_model

    def _complete_with_retry(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
    ) -> StructuredCompletion:
        """One structured call, retried once at 2x budget if truncated.

        Mirrors ``note_generation_service`` — but thinking is disabled here
        (``thinking_budget=0``). Import relocates the document's text into the
        SOAP shape verbatim; the model has nothing to reason about, so the
        reasoning the pro/thinking default spends is pure latency (measured
        ~90s vs ~10s on the same note). Generation keeps thinking on because
        there the reasoning is doing the work.
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
                    thinking_budget=0,
                )
            except StructuredOutputTruncatedError as exc:
                last_truncation = exc
                # Logs an integer max_output_tokens budget — not a credential.
                # nosemgrep
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
        # Plain registry shape (strings / lists) — the form grounding checks.
        registry_content = _coerce_registry_response(definition, data)
        grounding = check_grounding(registry_content, source_text)
        ungrounded = [g.path for g in grounding if not g.grounded]
        if ungrounded:
            # Field PATHS only — never the field text (no PHI in logs).
            logger.warning(
                "Imported note: %d/%d fields not grounded verbatim in source: %s",
                len(ungrounded),
                len(grounding),
                ungrounded,
            )
        # Store the SOAPSentence-shaped content a *generated* SOAP note uses, so
        # an imported note renders and edits in the note viewer identically.
        # source_segment_ids stay empty — there is no transcript to attribute to.
        content = _coerce_content_to_soap_note(registry_content).to_dict()
        return ParsedImportedNote(
            content=content,
            session_date=_parse_iso_date(data.get(_SESSION_DATE_KEY)),
            session_time=_parse_iso_time(data.get(_SESSION_TIME_KEY)),
            grounding=grounding,
        )
