# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Propose a practice note type from sample notes or a description.

A clinician should not have to write a definition by hand. Given one to
three notes they have already written, or a plain description of each
section, one structured model call proposes a :class:`PracticeNoteTypeSpec`:
the sections in the order the samples use them, a "what goes here" hint for
each field, a system prompt carrying the samples' style, and inputs only for
facts known before a visit. Nothing is saved — the proposal goes back to the
clinician, who saves it (or not) through the ordinary save route.

The proposal is then checked, without a model where possible:

- **Coverage.** Each sample is extracted into the proposed fields with the
  same verbatim relocation an imported note uses, and any passage that
  landed in no field is reported. That is the evidence the structure fits.
- **Copied text.** Labels, hints, the description and the prompt are kept;
  sample text is not. Anything that repeats a sample is rewritten once,
  then replaced with neutral wording if it still does, and reported.
- **A reference**, when one is named: its elements the proposal lacks come
  back as suggestions.

Samples are a client's record. They are used for this call and dropped:
never stored, never logged.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ValidationError

from ..notes.practice_types import PracticeNoteTypeSpec, to_definition
from ..notes.references import missing_elements
from ..settings import get_settings
from .ai_features import AIFeature
from .hedged_structured_llm_gateway import generation_gateway
from .note_type_derive_checks import (
    SampleText,
    copied_paths,
    split_passages,
    unplaced_passages,
    words,
)
from .note_type_derive_shape import drop_rationale_sections, ensure_encounter, strip_non_note
from .structured_llm_gateway import (
    StructuredLLMGateway,
    StructuredOutputTruncatedError,
)

if TYPE_CHECKING:
    from ..notes.references import NoteTypeReference, RequiredElement
    from .note_import_service import NoteImportService

logger = logging.getLogger(__name__)

MAX_SAMPLES = 3
MAX_SAMPLE_CHARS = 50_000
"""A written note is a few thousand characters; this bounds one model call."""
MAX_DESCRIPTION_CHARS = 20_000

DERIVE_SYSTEM_PROMPT = """\
You design note templates for a clinical practice. From the sample notes \
and/or the description you are given, propose the template the practice's \
notes follow.

Rules:
- Sections: one per heading or distinct part of the samples, in the order \
the samples use them. Each section has one or more fields.
- Fields: a short label, kind "list" when the samples write that part as \
items and "text" otherwise, and an ai_hint saying what goes in the field \
for any visit — the kind of content, not this visit's content.
- Each distinct labelled item or sub-heading in a sample section ("Treatment \
approach:", "Goal/plan:", "Medication changes:", "Follow-up:") is its own \
field. Never merge distinct items into one broad field such as "Plan" or \
"Notes"; a section is as many fields as the samples have items in it.
- Keys are lowercase snake_case, starting with a letter.
- system_prompt: instructions for drafting a note in this practice's \
style — prose or bullets, how quotes are used, register, tense and person. \
Describe the style; do not restate content.
- inputs: only facts the clinician knows before the visit starts (such as \
the visit type or place of service). Never a fact that comes from the \
conversation. Usually there are none.
- Visit facts in a note's header — date of service, start and end times, \
codes, place of service, where the client and the clinician were, telehealth \
consent — belong in a first section, Encounter, with a field for each, and a \
telehealth attestation field when the samples are telehealth visits. Place of \
service and the two locations are also inputs.
- Leave out anything a sample marks as not part of the note: explanations of \
how codes were chosen, rationale, teaching or reference material. Never \
propose a section whose purpose is justifying codes.
- Never copy a sample's wording into any label, hint, description or \
prompt. No names, dates, places, medications, quotes or events from a \
sample. A sample shows what kind of thing goes where; the template must \
work for every client.
"""

REWRITE_SYSTEM_PROMPT = """\
Each item below is part of a note template, and repeats wording from a \
specific client's note. Rewrite each one so it describes the kind of content \
in general terms that fit any visit, with no names, dates, places, \
medications, quotes or events, and no phrase taken from the original. Keep \
labels to a few words.
"""

NEUTRAL_LABEL = "Derived note type"
DERIVED_KEY = "custom.derived"
"""Key a proposal is extracted against for the coverage check; never stored."""

_MIN_CHOICE_OPTIONS = 2


class DeriveFailedError(ValueError):
    """The model did not produce a usable proposal."""


@dataclass(frozen=True)
class SampleCoverage:
    """How one sample fits the proposal.

    ``checked`` is False when the sample could not be extracted (the model
    call failed); the proposal still stands, without that evidence.
    """

    sample: int
    passages: int
    unplaced: list[str]
    checked: bool = True
    excluded: int = 0
    """Lines set aside as not note content: blocks the sample marks so (code
    rationale, teaching asides), signature lines, and facts that only identify
    the client or clinician. Reported apart from ``unplaced``."""


@dataclass(frozen=True)
class GuardFinding:
    path: str
    outcome: Literal["rewritten", "neutralized"]


@dataclass
class DerivedNoteType:
    spec: PracticeNoteTypeSpec
    coverage: list[SampleCoverage] = field(default_factory=list)
    guard: list[GuardFinding] = field(default_factory=list)
    suggestions: list[RequiredElement] = field(default_factory=list)
    repaired: bool = False


# ---------------------------------------------------------------------------
# Response schema: PracticeNoteTypeSpec's own JSON schema, in gateway dialect
# ---------------------------------------------------------------------------

_KEPT_KEYWORDS = ("type", "description", "enum", "required")


def _to_gateway_schema(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Inline ``$ref``s and keep only what every structured gateway accepts.

    Lengths and patterns are left out of what the model sees; validation
    against :class:`PracticeNoteTypeSpec` enforces them afterwards.
    """
    if "$ref" in node:
        return _to_gateway_schema(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
    out = {k: v for k, v in node.items() if k in _KEPT_KEYWORDS}
    if "properties" in node:
        out["properties"] = {
            name: _to_gateway_schema(sub, defs) for name, sub in node["properties"].items()
        }
    if "items" in node:
        out["items"] = _to_gateway_schema(node["items"], defs)
    return out


def derive_response_schema() -> dict[str, Any]:
    """The spec's schema without ``user_template``: a proposal never carries one.

    Titled, so the end-to-end stand-in can tell a derive call from a draft.
    """
    raw = PracticeNoteTypeSpec.model_json_schema()
    schema = _to_gateway_schema(raw, raw.get("$defs", {}))
    schema["properties"].pop("user_template", None)
    schema["title"] = "PracticeNoteTypeSpec"
    return schema


# ---------------------------------------------------------------------------
# Normalization: fix what is mechanical before validating
# ---------------------------------------------------------------------------

_MAX_KEY = 40


def _slug(raw: Any, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(raw or "").lower()).strip("_")
    if not slug or not slug[0].isalpha():
        slug = f"{fallback}_{slug}" if slug else fallback
    return slug[:_MAX_KEY].rstrip("_")


def _unique(key: str, seen: set[str]) -> str:
    candidate, n = key, 2
    while candidate in seen:
        suffix = f"_{n}"
        candidate = key[: _MAX_KEY - len(suffix)] + suffix
        n += 1
    seen.add(candidate)
    return candidate


def _text(raw: Any, limit: int) -> str:
    return str(raw or "").strip()[:limit]


def _normalize_field(raw: dict[str, Any], seen: set[str]) -> dict[str, Any]:
    label = _text(raw.get("label"), 80)
    return {
        "key": _unique(_slug(raw.get("key") or label, "field"), seen),
        "label": label,
        "kind": "list" if raw.get("kind") == "list" else "text",
        "ai_hint": _text(raw.get("ai_hint"), 2000),
    }


def _normalize_input(raw: dict[str, Any], seen: set[str]) -> dict[str, Any]:
    label = _text(raw.get("label"), 80)
    options = list(dict.fromkeys(_text(o, 200) for o in raw.get("options") or [] if o))[:20]
    is_choice = raw.get("kind") == "choice" and len(options) >= _MIN_CHOICE_OPTIONS
    return {
        "key": _unique(_slug(raw.get("key") or label, "input"), seen),
        "label": label,
        "kind": "choice" if is_choice else "text",
        "options": options if is_choice else [],
        "required": bool(raw.get("required")),
    }


def _normalize_section(raw: dict[str, Any], seen: set[str]) -> dict[str, Any]:
    label = _text(raw.get("label"), 80)
    field_keys: set[str] = set()
    return {
        "key": _unique(_slug(raw.get("key") or label, "section"), seen),
        "label": label,
        "fields": [
            _normalize_field(f, field_keys)
            for f in (raw.get("fields") or [])[:40]
            if isinstance(f, dict)
        ],
    }


def normalize_proposal(data: dict[str, Any]) -> dict[str, Any]:
    """Slug keys, de-duplicate them, trim to the spec's limits, drop a template."""
    section_keys: set[str] = set()
    input_keys: set[str] = set()
    return {
        "label": _text(data.get("label"), 80) or NEUTRAL_LABEL,
        "description": _text(data.get("description"), 1000),
        "system_prompt": _text(data.get("system_prompt"), 20_000),
        "sections": [
            _normalize_section(s, section_keys)
            for s in (data.get("sections") or [])[:20]
            if isinstance(s, dict)
        ],
        "inputs": [
            _normalize_input(i, input_keys)
            for i in (data.get("inputs") or [])[:10]
            if isinstance(i, dict)
        ],
    }


def _validation_summary(exc: ValidationError) -> str:
    """Where and why, never the rejected value."""
    return "\n".join(
        f"- {'.'.join(str(p) for p in err['loc']) or 'spec'}: {err['msg']}"
        for err in exc.errors(include_input=False, include_url=False)
    )


# ---------------------------------------------------------------------------
# Copied text: rewrite once, then neutralize
# ---------------------------------------------------------------------------

_STEP = re.compile(r"(\w+)(?:\[(\d+)\])?")


def _walk(spec: dict[str, Any], path: str) -> tuple[Any, str | int]:
    """The container and key or index a path from ``spec_texts`` names."""
    node: Any = spec
    steps = [_STEP.fullmatch(part) for part in path.split(".")]
    if any(step is None for step in steps):
        raise ValueError(f"not a spec path: {path!r}")
    resolved = [(s.group(1), s.group(2)) for s in steps if s is not None]
    for name, index in resolved[:-1]:
        node = node[name] if index is None else node[name][int(index)]
    name, index = resolved[-1]
    return (node, name) if index is None else (node[name], int(index))


def _get(spec: dict[str, Any], path: str) -> str:
    container, slot = _walk(spec, path)
    return str(container[slot])


def _set(spec: dict[str, Any], path: str, value: str) -> None:
    container, slot = _walk(spec, path)
    container[slot] = value


_GENERIC_FIELD_LABEL = "Details"
_GENERIC_SECTION_LABEL = "Notes"
_LABEL_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'&/+-]*")


def _owner(path: str) -> str:
    return path.rsplit(".", 1)[0]


def _is_section(path: str) -> bool:
    return path.startswith("sections[") and ".fields[" not in path


def _without(text: str, copied: set[str]) -> str:
    """``text`` with the words that made it a copy taken out."""
    kept = [w for w in _LABEL_WORD.findall(text) if not set(words(w)) & copied]
    cleaned = " ".join(kept).strip(" -/&")
    return cleaned[:1].upper() + cleaned[1:]


def _replacement_label(body: dict[str, Any], path: str, index: SampleText) -> str:
    """A label with the copied words removed, or a plain one when nothing is left.

    Never a positional name: a clinician reads these.
    """
    cleaned = _without(_get(body, path), index.copied_words(_get(body, path)))
    if cleaned and not index.copied(cleaned):
        return cleaned
    if path == "label":
        return NEUTRAL_LABEL
    if _is_section(path):
        section, slot = _walk(body, _owner(path))
        clean = [f["label"] for f in section[slot]["fields"] if not index.copied(f["label"])]
        return clean[0] if clean else _GENERIC_SECTION_LABEL
    return _GENERIC_FIELD_LABEL


def _rank(path: str) -> tuple[int, str]:
    # Field and input labels first, then section labels (which may borrow a
    # field's), then everything built from a label, then keys from labels.
    if path.endswith(".label") and not _is_section(path):
        return 0, path
    if path.endswith("label"):
        return 1, path
    if path.endswith(".key"):
        return 3, path
    return 2, path


def _settle(body: dict[str, Any], paths: list[str], index: SampleText) -> None:
    """Replace text at ``paths`` that still repeats a sample.

    Labels lose their copied words (or fall back to a plain label), hints
    describe their field in general terms, the description and prompt are
    cleared, and a key is rebuilt from its part's clean label. A choice
    input with a copied option is dropped: an option cannot be described in
    general terms.
    """
    dropped = {_input_of_option(p) for p in paths if ".options[" in p}
    for path in sorted((p for p in paths if ".options[" not in p), key=_rank):
        if path.endswith("label"):
            _set(body, path, _replacement_label(body, path, index))
        elif path.endswith(".key"):
            _set(body, path, _slug(_get(body, f"{_owner(path)}.label"), "part"))
        elif path in ("description", "system_prompt"):
            _set(body, path, "")
        else:
            _set(body, path, f"What the visit covered for {_get(body, f'{_owner(path)}.label')}.")
    if dropped:
        body["inputs"] = [item for n, item in enumerate(body["inputs"]) if n not in dropped]


def _input_of_option(path: str) -> int:
    return int(path[len("inputs[") : path.index("]")])


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


class NoteTypeDeriveService:
    """Propose a note type, then check it against the samples it came from."""

    def __init__(
        self,
        import_service: NoteImportService,
        llm_gateway: StructuredLLMGateway | None = None,
        model: str | None = None,
    ) -> None:
        self._import = import_service
        self._llm_gateway = llm_gateway or generation_gateway(AIFeature.NOTE_TYPE_DERIVE)
        self._model = model

    def derive(
        self,
        samples: list[str],
        description: str | None = None,
        reference: NoteTypeReference | None = None,
    ) -> DerivedNoteType:
        if not samples and not (description and description.strip()):
            raise ValueError("send at least one sample or a description")
        stripped = [strip_non_note(sample) for sample in samples]
        notes = [s.text for s in stripped]
        proposed, repaired = self._propose(notes, description)
        spec = _reshape(proposed, notes)
        result = DerivedNoteType(spec=spec, repaired=repaired)
        if samples:
            # The guard reads the samples whole: text copied from a block that
            # is not note content is still copied.
            result.spec, result.guard = self._guard(spec, samples)
            result.coverage = [
                replace(c, excluded=c.excluded + s.removed_lines)
                for c, s in zip(self.check_coverage(result.spec, notes), stripped, strict=True)
            ]
        if reference is not None:
            result.suggestions = missing_elements(reference, result.spec)
        # Counts only: the samples, and anything taken from them, stay out of logs.
        logger.info(
            "Derived note type: samples=%d sections=%d unplaced=%d guarded=%d repaired=%s",
            len(samples),
            len(result.spec.sections),
            sum(len(c.unplaced) for c in result.coverage),
            len(result.guard),
            repaired,
        )
        return result

    def _call(self, system_prompt: str, user_prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """One structured call at the note budget, retried once at 2x if truncated."""
        settings = get_settings()
        base = settings.note_max_output_tokens
        for budget in (base, base * 2):
            try:
                return self._llm_gateway.complete_structured(
                    model=self._model or settings.ai_model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_schema=schema,
                    max_output_tokens=budget,
                    temperature=0.2,
                    thinking_budget=settings.note_thinking_budget,
                ).data
            except StructuredOutputTruncatedError:
                logger.warning("Note type derive truncated at max_output_tokens=%d", budget)
        raise DeriveFailedError("The proposal was cut off twice.")

    def _propose(
        self, samples: list[str], description: str | None
    ) -> tuple[PracticeNoteTypeSpec, bool]:
        """One call, validated; on rejection, one repair call told what failed."""
        user_prompt = _derive_prompt(samples, description)
        schema = derive_response_schema()
        data = self._call(DERIVE_SYSTEM_PROMPT, user_prompt, schema)
        try:
            return PracticeNoteTypeSpec.model_validate(normalize_proposal(data)), False
        except ValidationError as exc:
            repair = (
                f"{user_prompt}\n\n# Your previous proposal was rejected\n"
                f"{_validation_summary(exc)}\n\nPropose the template again, fixing these."
            )
        data = self._call(DERIVE_SYSTEM_PROMPT, repair, schema)
        try:
            return PracticeNoteTypeSpec.model_validate(normalize_proposal(data)), True
        except ValidationError as exc:
            raise DeriveFailedError("The proposal did not validate after one repair.") from exc

    def _guard(
        self, spec: PracticeNoteTypeSpec, samples: list[str]
    ) -> tuple[PracticeNoteTypeSpec, list[GuardFinding]]:
        """Rewrite sample-specific text once; replace whatever still repeats a sample."""
        index = SampleText(samples)
        flagged = copied_paths(spec, index)
        if not flagged:
            return spec, []
        body = spec.model_dump(mode="json")
        # Keys and options are structure: they are rebuilt or dropped, not rewritten.
        rewritable = [p for p in flagged if not p.endswith(".key") and ".options[" not in p]
        if rewritable:
            self._rewrite(body, rewritable)
        replaced = [p for p in flagged if p not in rewritable]
        replaced += [
            p
            for p in rewritable
            if index.copied(_get(body, p), heading_allowed=p.endswith("label"))
        ]
        _settle(body, replaced, index)
        guarded = PracticeNoteTypeSpec.model_validate(normalize_proposal(body))
        # Plain wording built from a label could, in principle, match a sample too.
        leftover = [p for p in copied_paths(guarded, index) if p not in replaced]
        if leftover:
            body = guarded.model_dump(mode="json")
            _settle(body, leftover, index)
            guarded = PracticeNoteTypeSpec.model_validate(normalize_proposal(body))
            replaced += leftover
        findings = [GuardFinding(p, "neutralized") for p in replaced]
        findings += [GuardFinding(p, "rewritten") for p in rewritable if p not in replaced]
        return guarded, sorted(findings, key=lambda f: f.path)

    def _rewrite(self, body: dict[str, Any], paths: list[str]) -> None:
        """Ask once for general wording at each path; a failed call changes nothing."""
        names = {f"item_{n}": path for n, path in enumerate(paths)}
        prompt = "\n".join(f"- {name} ({path}): {_get(body, path)}" for name, path in names.items())
        schema = {
            "type": "object",
            "properties": {name: {"type": "string"} for name in names},
            "required": list(names),
        }
        try:
            data = self._call(REWRITE_SYSTEM_PROMPT, prompt, schema)
        except (ValueError, RuntimeError):
            logger.warning("Note type derive rewrite failed; neutralizing %d items", len(paths))
            return
        for name, path in names.items():
            text = str(data.get(name) or "").strip()
            if text:
                limit = 80 if path.endswith("label") else 2000
                _set(body, path, text[:limit])

    def check_coverage(
        self, spec: PracticeNoteTypeSpec, samples: list[str]
    ) -> list[SampleCoverage]:
        """Extract each sample into ``spec`` and report the passages left over."""
        definition = to_definition(DERIVED_KEY, 0, spec)
        coverage: list[SampleCoverage] = []
        for n, sample in enumerate(samples):
            found, set_aside = split_passages(sample)
            try:
                extracted = self._import.extract_into(definition, sample)
            except ValueError:
                logger.warning("Note type derive: sample %d could not be extracted", n)
                coverage.append(
                    SampleCoverage(n, len(found), [], checked=False, excluded=set_aside)
                )
                continue
            coverage.append(
                SampleCoverage(
                    n, len(found), unplaced_passages(sample, extracted), excluded=set_aside
                )
            )
        return coverage


def _reshape(spec: PracticeNoteTypeSpec, notes: list[str]) -> PracticeNoteTypeSpec:
    """Drop code-justification sections; give the samples' visit facts a home."""
    body = spec.model_dump(mode="json")
    kept = list(body["sections"])
    drop_rationale_sections(body)
    if not body["sections"]:
        body["sections"] = kept
    if notes:
        ensure_encounter(body, notes)
    return PracticeNoteTypeSpec.model_validate(normalize_proposal(body))


def _derive_prompt(samples: list[str], description: str | None) -> str:
    parts: list[str] = []
    if description and description.strip():
        parts.append(f"# Description of the practice's notes\n{description.strip()}")
    for n, sample in enumerate(samples, start=1):
        parts.append(f'# Sample note {n}\n"""\n{sample}\n"""')
    parts.append("Propose the note template.")
    return "\n\n".join(parts)
