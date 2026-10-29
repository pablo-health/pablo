# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Deterministic checks on a note type derived from sample notes.

Two questions are answered here without a model:

- **Did any sample text get copied into the definition?** A derived type is
  stored and shown to everyone in the practice, while its samples are a
  client's record. Labels, hints, the description and the prompt must
  describe what goes in a note, never repeat what one said.
  :class:`SampleText` finds text specific to a sample — its names, its
  quotations, and shared runs of words that carry distinctive words —
  while headings and shared clinical vocabulary never count.
- **Does the definition have a place for everything the samples hold?**
  :func:`unplaced_passages` compares each passage of a sample with what was
  extracted from it into the proposed fields; a passage that landed nowhere
  is one the clinician should see.

Both work on lowercased word tokens, so case, punctuation and line breaks
do not hide a match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .note_type_derive_vocabulary import CLINICAL_VOCABULARY

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from ..notes.practice_types import PracticeNoteTypeSpec

COPY_RUN_WORDS = 6
"""Any run of this many consecutive words shared with a sample is a copy."""

SHORT_NGRAM = 3
COPY_SHORT_RATIO = 0.5
"""Or: at least this share of a text's three-word sequences occur in a sample.

Catches a short hint that copies a phrase and changes a word in it. Texts
under four words (most labels) are never flagged by this alone.
"""
MIN_SHORT_NGRAMS = 2

PLACED_RATIO = 0.5
"""A passage is placed when this share of its three-word sequences was extracted."""

MIN_PASSAGE_WORDS = 4
"""Shorter passages (headings, field labels) carry no content of their own."""

MAX_UNPLACED_PER_SAMPLE = 50


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _ngrams(tokens: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


MIN_NAME_LETTERS = 3


def _split_heading(line: str) -> tuple[str | None, str]:
    """A line's heading, if it carries one, and the text after it.

    Headings are lines ending in a colon or written in capitals, and the
    short label before the colon in "Interval history: slept better".
    """
    if line.endswith(":") or (line.upper() == line and any(c.isalpha() for c in line)):
        return line, ""
    head, sep, rest = line.partition(":")
    if sep and len(words(head)) <= MIN_PASSAGE_WORDS * 2:
        return head, rest.strip()
    return None, line


def _sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+", text.strip())


_NAME_LABELS = frozenset({"client", "patient", "name", "full"})
"""A heading made only of these words ("Client:", "Patient name:") labels a name."""

_CAPITALIZED_RUN = re.compile(r"\b[A-Z][a-z][A-Za-z'-]*(?:[ \t]+[A-Z][a-z][A-Za-z'-]*)+")


def _capitalized(body: str) -> set[str]:
    """Every capitalized word in ``body``, wherever it stands."""
    found: set[str] = set()
    for raw in re.findall(r"[A-Za-z][A-Za-z'-]*", body):
        if raw[0].isupper() and not raw.isupper():
            found |= {w for w in words(raw) if len(w) >= MIN_NAME_LETTERS}
    return found


def _capitalized_inside_sentences(body: str) -> set[str]:
    """Words capitalized somewhere other than the start of a sentence."""
    found: set[str] = set()
    for sentence in _sentences(body):
        for raw in re.findall(r"[A-Za-z][A-Za-z'-]*", sentence)[1:]:
            if raw[0].isupper() and not raw.isupper():
                found |= {w for w in words(raw) if len(w) >= MIN_NAME_LETTERS}
    return found


MIN_DISTINCTIVE = 2
"""A shared run of words is a copy only if it carries this many distinctive words."""

MIN_QUOTE_WORDS = 3

_QUOTED = re.compile(r"[\"“]([^\"“”]{3,300})[\"”]")


def distinctive(token: str) -> bool:
    """A word that can say something about one client: not shared clinical vocabulary.

    Numbers count (doses, dates, identifiers); short words do not.
    """
    if any(c.isdigit() for c in token):
        return True
    return len(token) >= MIN_NAME_LETTERS and token not in CLINICAL_VOCABULARY


def _distinctive_in(grams: set[tuple[str, ...]]) -> int:
    return len({t for gram in grams for t in gram if distinctive(t)})


def _contains(tokens: list[str], run: tuple[str, ...]) -> bool:
    width = len(run)
    return any(tuple(tokens[i : i + width]) == run for i in range(len(tokens) - width + 1))


class SampleText:
    """The samples, indexed for finding text specific to them.

    Text is a copy when it carries one of the samples' names (a word
    capitalized inside a sentence and never written in lowercase, and not
    clinical vocabulary), repeats something a sample quotes, or shares a
    run of words with a sample that holds at least :data:`MIN_DISTINCTIVE`
    distinctive words. Headings and generic clinical wording are shared by
    every note, so they are never a copy on their own.
    """

    def __init__(self, samples: Iterable[str]) -> None:
        self._runs: set[tuple[str, ...]] = set()
        self._short: set[tuple[str, ...]] = set()
        self._quotes: set[tuple[str, ...]] = set()
        # A proposal names its sections after the samples' headings — that
        # is the structure being derived, not content — so a label equal to
        # one is allowed.
        self._headings: set[tuple[str, ...]] = set()
        capitalized: set[str] = set()
        lowercase: set[str] = set()
        texts = list(samples)
        for sample in texts:
            tokens = words(sample)
            self._runs |= _ngrams(tokens, COPY_RUN_WORDS)
            self._short |= _ngrams(tokens, SHORT_NGRAM)
            lowercase |= set(re.findall(r"\b[a-z][a-z0-9]*\b", sample))
            for quoted in _QUOTED.findall(sample):
                quote = tuple(words(quoted))
                if len(quote) >= MIN_QUOTE_WORDS:
                    self._quotes.add(quote)
            for raw in sample.splitlines():
                heading, body = _split_heading(raw.strip())
                if heading:
                    self._headings.add(tuple(words(heading)))
                    if set(words(heading)) <= _NAME_LABELS:
                        # "Client: Quill Harbinger" — the value is a name.
                        capitalized |= _capitalized(body)
                capitalized |= _capitalized_inside_sentences(body)
        names = {n for n in capitalized - lowercase if distinctive(n)}
        # A capitalized word beside a name is part of it: the first name that
        # opens a sentence ("Quill reports...") is found through "Quill Harbinger".
        for sample in texts:
            for run in _CAPITALIZED_RUN.findall(sample):
                run_words = {w for w in words(run) if distinctive(w)}
                if run_words & names:
                    names |= run_words - lowercase
        self._names = names

    def copied_words(self, text: str) -> set[str]:
        """The words of ``text`` that make it a copy; empty when it is not one."""
        tokens = words(text)
        found = self._names.intersection(tokens)
        for quote in self._quotes:
            if _contains(tokens, quote):
                found |= {t for t in quote if distinctive(t)} or set(quote)
        shared_runs = _ngrams(tokens, COPY_RUN_WORDS) & self._runs
        if _distinctive_in(shared_runs) >= MIN_DISTINCTIVE:
            found |= {t for gram in shared_runs for t in gram if distinctive(t)}
        short = _ngrams(tokens, SHORT_NGRAM)
        if len(short) >= MIN_SHORT_NGRAMS:
            shared = short & self._short
            if (
                len(shared) / len(short) >= COPY_SHORT_RATIO
                and _distinctive_in(shared) >= MIN_DISTINCTIVE
            ):
                found |= {t for gram in shared for t in gram if distinctive(t)}
        return found

    def copied(self, text: str, *, heading_allowed: bool = False) -> bool:
        if heading_allowed and tuple(words(text)) in self._headings:
            return False
        return bool(self.copied_words(text))


@dataclass(frozen=True)
class SpecText:
    """One piece of a proposal's text, addressed by where it sits."""

    path: str
    text: str
    is_label: bool = False


def spec_texts(spec: PracticeNoteTypeSpec) -> Iterator[SpecText]:
    """Every piece of a proposal a person reads or a model is prompted with."""
    yield SpecText("label", spec.label, is_label=True)
    yield SpecText("description", spec.description)
    yield SpecText("system_prompt", spec.system_prompt)
    for i, section in enumerate(spec.sections):
        base = f"sections[{i}]"
        yield SpecText(f"{base}.key", section.key.replace("_", " "), is_label=True)
        yield SpecText(f"{base}.label", section.label, is_label=True)
        for j, fld in enumerate(section.fields):
            yield SpecText(f"{base}.fields[{j}].key", fld.key.replace("_", " "), is_label=True)
            yield SpecText(f"{base}.fields[{j}].label", fld.label, is_label=True)
            yield SpecText(f"{base}.fields[{j}].ai_hint", fld.ai_hint)
    for k, item in enumerate(spec.inputs):
        yield SpecText(f"inputs[{k}].key", item.key.replace("_", " "), is_label=True)
        yield SpecText(f"inputs[{k}].label", item.label, is_label=True)
        for m, option in enumerate(item.options):
            yield SpecText(f"inputs[{k}].options[{m}]", option, is_label=True)


def copied_paths(spec: PracticeNoteTypeSpec, samples: SampleText) -> list[str]:
    """Paths of the proposal's text that repeats a sample."""
    return [
        part.path
        for part in spec_texts(spec)
        if part.text and samples.copied(part.text, heading_allowed=part.is_label)
    ]


# "Label: value" inside a line. A label starts with a capital and runs up to
# six words of label characters; a comma or full stop cannot be inside one, so
# "October 20, 2025 CPT Codes:" yields the label "CPT Codes".
_LABEL = re.compile(
    # A time's AM or PM ends a value; it never starts a label ("10:45 AM Diagnosis:").
    # Words in a label are one space apart: a wider gap separates columns.
    r"(?:^|(?<=\s))(?!(?:AM|PM)\b)([A-Z][\w/&()'-]*(?: [\w/&()'-]+){0,5}):[ \t]+"
)

# Labels that name who the note is about or by, rather than what happened.
_IDENTITY_WORDS = frozenset(
    {
        "client",
        "patient",
        "name",
        "full",
        "dob",
        "date",
        "of",
        "birth",
        "provider",
        "clinician",
        "mrn",
        "id",
        "age",
        "sex",
        "gender",
        "pronouns",
        "s",
    }
)

_CREDENTIALS = frozenset(
    {
        "md",
        "do",
        "np",
        "pmhnp",
        "fnp",
        "bc",
        "rn",
        "lcsw",
        "lpc",
        "lmft",
        "lmhc",
        "phd",
        "psyd",
        "pa",
        "aprn",
        "dnp",
        "msw",
        "lisw",
        "lpcc",
        "ma",
        "ms",
    }
)


@dataclass(frozen=True)
class Passage:
    """One unit of a sample the coverage check looks for: a sentence or a labelled fact.

    ``value`` is what a field would hold — the text after its label, or the
    whole sentence when it has none.
    """

    text: str
    value: str


def _segments(line: str) -> list[tuple[str, str]]:
    """``(text, value)`` for each labelled fact on a line, or the line itself."""
    starts = list(_LABEL.finditer(line))
    if not starts:
        return [(line, line)]
    pieces: list[tuple[str, str]] = []
    if starts[0].start() > 0:
        lead = line[: starts[0].start()].strip()
        pieces.append((lead, lead))
    for n, match in enumerate(starts):
        end = starts[n + 1].start() if n + 1 < len(starts) else len(line)
        pieces.append((line[match.start() : end].strip(), line[match.end() : end].strip()))
    return pieces


def _is_identity(text: str) -> bool:
    match = _LABEL.match(text)
    if match is None:
        return False
    return set(words(match.group(1))) <= _IDENTITY_WORDS


def _is_signature(line: str) -> bool:
    """A short line naming a clinician by credentials: "Sam Sample, MD 4/1/2026"."""
    if _LABEL.search(line):
        return False
    tokens = words(line)
    return (
        0 < len(tokens) <= MIN_PASSAGE_WORDS * 2
        and bool(_CREDENTIALS & set(tokens))
        and ("," in line or line.lower().startswith(("signed", "electronically signed")))
    )


def _is_structure(line: str) -> bool:
    """A line that names or introduces content rather than holding it.

    A short heading ("Mental Status Exam (MSE)"), or any line ending in a
    colon ("Follow-up and next steps:", "I am making these changes:"),
    whose content is on the lines after it.
    """
    text = line.rstrip()
    if text.endswith(":"):
        return True
    return (
        len(words(text)) <= MIN_PASSAGE_WORDS * 2
        and not text.endswith((".", "!", "?"))
        and ":" not in text
    )


def split_passages(sample: str) -> tuple[list[Passage], int]:
    """A sample's passages, and how many lines were set aside as not note content.

    Each labelled fact on a line is its own passage, so a header line
    carrying a date and the codes counts as two. Headings carry no content.
    Signature lines and facts that only identify the client or clinician
    (name, date of birth) are set aside: they belong to the record, not the
    note's body.
    """
    found: list[Passage] = []
    excluded = 0
    for raw in sample.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _is_signature(line):
            excluded += 1
            continue
        if _is_structure(line):
            continue
        for text, value in _segments(line):
            if _is_identity(text):
                excluded += 1
                continue
            if text != value:  # a labelled fact counts however short it is
                if words(value):
                    found.append(Passage(text, value))
                continue
            for sentence in _sentences(value):
                if len(words(sentence)) >= MIN_PASSAGE_WORDS:
                    found.append(Passage(sentence.strip(), sentence.strip()))
    return found, excluded


def passages(sample: str) -> list[str]:
    """The text of each passage :func:`split_passages` finds."""
    return [p.text for p in split_passages(sample)[0]]


def _content_tokens(content: dict[str, Any]) -> Iterator[list[str]]:
    for fields in content.values():
        if not isinstance(fields, dict):
            continue
        for value in fields.values():
            items = value if isinstance(value, list) else [value]
            for item in items:
                if item:
                    yield words(str(item))


def unplaced_passages(sample: str, extracted: dict[str, Any]) -> list[str]:
    """The sample's passages that the extraction put in no field."""
    placed: set[tuple[str, ...]] = set()
    placed_words: set[str] = set()
    for tokens in _content_tokens(extracted):
        placed |= _ngrams(tokens, SHORT_NGRAM)
        placed_words |= set(tokens)
    unplaced: list[str] = []
    for passage in split_passages(sample)[0]:
        # The value is what a field holds; the label is the field's own name.
        tokens = words(passage.value)
        grams = _ngrams(tokens, SHORT_NGRAM)
        if grams:
            share = len(grams & placed) / len(grams)
        else:
            share = sum(t in placed_words for t in tokens) / len(tokens)
        if share < PLACED_RATIO:
            unplaced.append(passage.text)
        if len(unplaced) >= MAX_UNPLACED_PER_SAMPLE:
            break
    return unplaced
