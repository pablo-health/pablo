# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Deterministic checks on a note type derived from sample notes.

Two questions are answered here without a model:

- **Did any sample text get copied into the definition?** A derived type is
  stored and shown to everyone in the practice, while its samples are a
  client's record. Labels, hints, the description and the prompt must
  describe what goes in a note, never repeat what one said.
  :class:`SampleText` finds copied text by word n-gram overlap, and finds
  a sample's names copied on their own.
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


def _capitalized_inside_sentences(body: str) -> set[str]:
    """Words capitalized somewhere other than the start of a sentence."""
    found: set[str] = set()
    for sentence in _sentences(body):
        for raw in re.findall(r"[A-Za-z][A-Za-z'-]*", sentence)[1:]:
            if raw[0].isupper() and not raw.isupper():
                found |= {w for w in words(raw) if len(w) >= MIN_NAME_LETTERS}
    return found


class SampleText:
    """The samples, indexed for finding text copied out of them.

    Besides shared word runs, a single word is a copy when it is one of the
    samples' names: capitalized inside a sentence and never written in
    lowercase anywhere in them. A name copied alone shares no run of words
    with the sample, and a name is exactly what must not travel.
    """

    def __init__(self, samples: Iterable[str]) -> None:
        self._runs: set[tuple[str, ...]] = set()
        self._short: set[tuple[str, ...]] = set()
        # A proposal names its sections after the samples' headings — that
        # is the structure being derived, not content — so a label equal to
        # one is allowed.
        self._headings: set[tuple[str, ...]] = set()
        capitalized: set[str] = set()
        lowercase: set[str] = set()
        for sample in samples:
            tokens = words(sample)
            self._runs |= _ngrams(tokens, COPY_RUN_WORDS)
            self._short |= _ngrams(tokens, SHORT_NGRAM)
            lowercase |= set(re.findall(r"\b[a-z][a-z0-9]*\b", sample))
            for raw in sample.splitlines():
                heading, body = _split_heading(raw.strip())
                if heading:
                    self._headings.add(tuple(words(heading)))
                capitalized |= _capitalized_inside_sentences(body)
        self._names = capitalized - lowercase

    def copied(self, text: str, *, heading_allowed: bool = False) -> bool:
        tokens = words(text)
        if heading_allowed and tuple(tokens) in self._headings:
            return False
        if self._names.intersection(tokens):
            return True
        if _ngrams(tokens, COPY_RUN_WORDS) & self._runs:
            return True
        short = _ngrams(tokens, SHORT_NGRAM)
        if len(short) < MIN_SHORT_NGRAMS:
            return False
        return len(short & self._short) / len(short) >= COPY_SHORT_RATIO


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


def passages(sample: str) -> list[str]:
    """A sample's sentences, one per line or full stop, long enough to mean something."""
    found: list[str] = []
    for line in sample.splitlines():
        for sentence in re.split(r"(?<=[.!?])\s+", line.strip()):
            if len(words(sentence)) >= MIN_PASSAGE_WORDS:
                found.append(sentence.strip())
    return found


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
    for passage in passages(sample):
        tokens = words(passage)
        grams = _ngrams(tokens, SHORT_NGRAM)
        if grams:
            share = len(grams & placed) / len(grams)
        else:
            share = sum(t in placed_words for t in tokens) / len(tokens)
        if share < PLACED_RATIO:
            unplaced.append(passage)
        if len(unplaced) >= MAX_UNPLACED_PER_SAMPLE:
            break
    return unplaced
