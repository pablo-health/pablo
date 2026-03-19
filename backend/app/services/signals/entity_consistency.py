# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Entity consistency safety signal for the hybrid verification pipeline.

Detects entity mismatches between claim and segment text. Catches errors
like citing the wrong medication, dosage, frequency, or person reference.

This signal NEVER returns PASS -- only FAIL (entity mismatch) or UNCERTAIN.
"""

from __future__ import annotations

import re

from app.services.verification_signals import (
    SignalContext,
    SignalResult,
    SignalVerdict,
    VerificationSignal,
)

# --- Medication dictionaries ---

SSRIS: set[str] = {
    "sertraline",
    "fluoxetine",
    "escitalopram",
    "paroxetine",
    "citalopram",
    "fluvoxamine",
    "vilazodone",
    "vortioxetine",
}

SNRIS: set[str] = {
    "venlafaxine",
    "duloxetine",
    "desvenlafaxine",
    "levomilnacipran",
    "milnacipran",
}

BENZODIAZEPINES: set[str] = {
    "alprazolam",
    "lorazepam",
    "clonazepam",
    "diazepam",
    "chlordiazepoxide",
    "oxazepam",
    "temazepam",
    "triazolam",
    "midazolam",
}

ANTIPSYCHOTICS: set[str] = {
    "aripiprazole",
    "quetiapine",
    "olanzapine",
    "risperidone",
    "ziprasidone",
    "lurasidone",
    "paliperidone",
    "clozapine",
    "haloperidol",
    "brexpiprazole",
    "cariprazine",
}

MOOD_STABILIZERS: set[str] = {
    "lithium",
    "valproate",
    "lamotrigine",
    "carbamazepine",
    "oxcarbazepine",
    "topiramate",
}

STIMULANTS: set[str] = {
    "methylphenidate",
    "amphetamine",
    "dextroamphetamine",
    "lisdexamfetamine",
    "atomoxetine",
    "modafinil",
}

SLEEP_AIDS: set[str] = {
    "zolpidem",
    "eszopiclone",
    "suvorexant",
    "lemborexant",
    "trazodone",
    "hydroxyzine",
    "melatonin",
    "doxepin",
    "ramelteon",
}

OTHER_PSYCHIATRIC: set[str] = {
    "buspirone",
    "gabapentin",
    "pregabalin",
    "propranolol",
    "clonidine",
    "guanfacine",
    "naltrexone",
    "buprenorphine",
    "methadone",
    "bupropion",
    "mirtazapine",
    "nortriptyline",
    "amitriptyline",
    "imipramine",
    "desipramine",
    "phenelzine",
    "tranylcypromine",
    "selegiline",
}

ALL_GENERIC_MEDICATIONS: set[str] = (
    SSRIS
    | SNRIS
    | BENZODIAZEPINES
    | ANTIPSYCHOTICS
    | MOOD_STABILIZERS
    | STIMULANTS
    | SLEEP_AIDS
    | OTHER_PSYCHIATRIC
)

BRAND_TO_GENERIC: dict[str, str] = {
    # SSRIs
    "zoloft": "sertraline",
    "prozac": "fluoxetine",
    "lexapro": "escitalopram",
    "paxil": "paroxetine",
    "celexa": "citalopram",
    "luvox": "fluvoxamine",
    "viibryd": "vilazodone",
    "trintellix": "vortioxetine",
    # SNRIs
    "effexor": "venlafaxine",
    "cymbalta": "duloxetine",
    "pristiq": "desvenlafaxine",
    "fetzima": "levomilnacipran",
    # Benzodiazepines
    "xanax": "alprazolam",
    "ativan": "lorazepam",
    "klonopin": "clonazepam",
    "valium": "diazepam",
    "librium": "chlordiazepoxide",
    "halcion": "triazolam",
    "restoril": "temazepam",
    # Antipsychotics
    "abilify": "aripiprazole",
    "seroquel": "quetiapine",
    "zyprexa": "olanzapine",
    "risperdal": "risperidone",
    "geodon": "ziprasidone",
    "latuda": "lurasidone",
    "invega": "paliperidone",
    "clozaril": "clozapine",
    "haldol": "haloperidol",
    "rexulti": "brexpiprazole",
    "vraylar": "cariprazine",
    # Mood stabilizers
    "depakote": "valproate",
    "lamictal": "lamotrigine",
    "tegretol": "carbamazepine",
    "trileptal": "oxcarbazepine",
    "topamax": "topiramate",
    # Stimulants
    "ritalin": "methylphenidate",
    "concerta": "methylphenidate",
    "adderall": "amphetamine",
    "dexedrine": "dextroamphetamine",
    "vyvanse": "lisdexamfetamine",
    "strattera": "atomoxetine",
    "provigil": "modafinil",
    # Sleep aids
    "ambien": "zolpidem",
    "lunesta": "eszopiclone",
    "belsomra": "suvorexant",
    "dayvigo": "lemborexant",
    "silenor": "doxepin",
    "rozerem": "ramelteon",
    # Other
    "buspar": "buspirone",
    "neurontin": "gabapentin",
    "lyrica": "pregabalin",
    "inderal": "propranolol",
    "catapres": "clonidine",
    "intuniv": "guanfacine",
    "vivitrol": "naltrexone",
    "suboxone": "buprenorphine",
    "wellbutrin": "bupropion",
    "remeron": "mirtazapine",
    "pamelor": "nortriptyline",
    "elavil": "amitriptyline",
    "tofranil": "imipramine",
    "nardil": "phenelzine",
    "parnate": "tranylcypromine",
    "emsam": "selegiline",
}

# --- Regex patterns ---

DOSAGE_PATTERN = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(mg|mcg|ml|g|iu|units?|%)\b",
    re.IGNORECASE,
)

FREQUENCY_PATTERN = re.compile(
    r"\b(\d+)\s*(?:times?|x|episodes?)\s*(?:per|a|/)\s*(day|week|month|year)\b",
    re.IGNORECASE,
)

FREQUENCY_WORDS: dict[str, str] = {
    "daily": "1/day",
    "twice daily": "2/day",
    "bid": "2/day",
    "tid": "3/day",
    "qid": "4/day",
    "weekly": "1/week",
    "twice weekly": "2/week",
    "biweekly": "1/2weeks",
    "monthly": "1/month",
    "once daily": "1/day",
    "once weekly": "1/week",
    "once monthly": "1/month",
    "once a day": "1/day",
    "once a week": "1/week",
    "once a month": "1/month",
    "twice a day": "2/day",
    "twice a week": "2/week",
    "three times a day": "3/day",
    "three times a week": "3/week",
}

DSM_CODE_PATTERN = re.compile(r"\bF\d{2}(?:\.\d{1,2})?\b")

PERSON_ROLES: set[str] = {
    "therapist",
    "client",
    "patient",
    "clinician",
    "doctor",
    "psychiatrist",
    "counselor",
    "psychologist",
    "provider",
    "practitioner",
}

PERSON_ROLE_PATTERN = re.compile(
    r"\b(" + "|".join(PERSON_ROLES) + r")\b",
    re.IGNORECASE,
)


def _normalize_medication(text: str) -> str | None:
    """Normalize a medication name to its generic form."""
    lower = text.lower()
    if lower in ALL_GENERIC_MEDICATIONS:
        return lower
    return BRAND_TO_GENERIC.get(lower)


def _extract_medications(text: str) -> set[str]:
    """Extract normalized medication names from text."""
    words = re.findall(r"\b[a-zA-Z]{4,}\b", text)
    meds: set[str] = set()
    for word in words:
        generic = _normalize_medication(word)
        if generic:
            meds.add(generic)
    return meds


def _extract_dosages(text: str) -> set[str]:
    """Extract dosage values from text (e.g., '20mg', '0.5 mg')."""
    return {f"{m.group(1)}{m.group(2).lower()}" for m in DOSAGE_PATTERN.finditer(text)}


def _extract_frequencies(text: str) -> set[str]:
    """Extract frequency expressions from text."""
    freqs: set[str] = set()

    for match in FREQUENCY_PATTERN.finditer(text):
        freqs.add(f"{match.group(1)}/{match.group(2).lower()}")

    lower = text.lower()
    for phrase, normalized in FREQUENCY_WORDS.items():
        if phrase in lower:
            freqs.add(normalized)

    return freqs


def _extract_dsm_codes(text: str) -> set[str]:
    """Extract DSM-5/ICD-10 codes from text."""
    return set(DSM_CODE_PATTERN.findall(text))


def _extract_person_roles(text: str) -> set[str]:
    """Extract person role references from text."""
    return {m.group(1).lower() for m in PERSON_ROLE_PATTERN.finditer(text)}


def _categorize_roles(roles: set[str]) -> set[str]:
    """Map specific roles to provider/client categories for comparison."""
    provider_roles = {
        "therapist",
        "clinician",
        "doctor",
        "psychiatrist",
        "counselor",
        "psychologist",
        "provider",
        "practitioner",
    }
    client_roles = {"client", "patient"}
    categories: set[str] = set()
    for role in roles:
        if role in provider_roles:
            categories.add("provider")
        elif role in client_roles:
            categories.add("client")
    return categories


class EntityConsistencySignal(VerificationSignal):
    """Check that entities in claim match entities in segment.

    This is a safety signal: it NEVER returns PASS. It returns FAIL when
    entities are present in both texts but mismatch, and UNCERTAIN otherwise.
    """

    @property
    def name(self) -> str:
        return "entity_consistency"

    def check(
        self,
        claim_text: str,
        segment_text: str,
        context: SignalContext,  # noqa: ARG002
    ) -> SignalResult:
        mismatches: list[str] = []

        # Check medication mismatches
        claim_meds = _extract_medications(claim_text)
        segment_meds = _extract_medications(segment_text)
        if claim_meds and segment_meds and not claim_meds & segment_meds:
            mismatches.append(
                f"medications: claim={sorted(claim_meds)}, " f"segment={sorted(segment_meds)}"
            )

        # Check dosage mismatches
        claim_dosages = _extract_dosages(claim_text)
        segment_dosages = _extract_dosages(segment_text)
        if claim_dosages and segment_dosages and not claim_dosages & segment_dosages:
            mismatches.append(
                f"dosages: claim={sorted(claim_dosages)}, " f"segment={sorted(segment_dosages)}"
            )

        # Check frequency mismatches
        claim_freqs = _extract_frequencies(claim_text)
        segment_freqs = _extract_frequencies(segment_text)
        if claim_freqs and segment_freqs and not claim_freqs & segment_freqs:
            mismatches.append(
                f"frequencies: claim={sorted(claim_freqs)}, " f"segment={sorted(segment_freqs)}"
            )

        # Check DSM code mismatches
        claim_dsm = _extract_dsm_codes(claim_text)
        segment_dsm = _extract_dsm_codes(segment_text)
        if claim_dsm and segment_dsm and not claim_dsm & segment_dsm:
            mismatches.append(
                f"DSM codes: claim={sorted(claim_dsm)}, " f"segment={sorted(segment_dsm)}"
            )

        # Check person role mismatches (provider vs client attribution)
        claim_roles = _categorize_roles(_extract_person_roles(claim_text))
        segment_roles = _categorize_roles(_extract_person_roles(segment_text))
        if claim_roles and segment_roles and not claim_roles & segment_roles:
            mismatches.append(
                f"person roles: claim={sorted(claim_roles)}, " f"segment={sorted(segment_roles)}"
            )

        if mismatches:
            return SignalResult(
                verdict=SignalVerdict.FAIL,
                confidence=0.1,
                signal_name=self.name,
                detail=f"Entity mismatch: {'; '.join(mismatches)}",
            )

        return SignalResult(
            verdict=SignalVerdict.UNCERTAIN,
            confidence=0.5,
            signal_name=self.name,
            detail="No entity mismatch detected",
        )
