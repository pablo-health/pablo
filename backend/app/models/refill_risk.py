# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a refill request says about time and risk: when the client runs out,
whether the medication is controlled, and whether it is harmful to interrupt.

Three facts, read for the practice and never shown to the client:

``run_out_date``
    The client's own answer when they asked ("how many days do you have
    left?"), stored on the request. Without one, an estimate from the most
    recent signed prescription for the same drug: the day it was written plus
    ``days_supply`` for the first fill and each refill it allowed. The engine
    keeps no dispensing record, so the written date stands in for the first
    fill and each refill is assumed to be taken on time; the estimate is
    therefore the latest the supply could last, never an early alarm. The
    client's answer wins over the estimate: they know what is in the bottle.

``controlled``
    Schedule II-V, from that prescription's ``schedule`` when one is on file;
    otherwise the clinician's own ``patient_medications.controlled``. For
    information: a DEA schedule measures misuse potential, not the harm of a
    missed dose, so nothing here treats it as urgency.

``interruption_risk``
    Whether a gap in the medication is itself harmful: a dangerous withdrawal,
    a restart that has to be re-titrated, a rebound or a relapse. The
    clinician's ``patient_medications.interruption_risk`` when set, either
    way; otherwise the default list below. A clinical call is the
    clinician's: the list is a starting point, not a ruling.

**The default list.** Every entry cites the labeling or guideline that says
interrupting it is harmful. A candidate that could not be cited is left out
rather than guessed at (butalbital: its labeling describes withdrawal
seizures only in a newborn, and it is usually taken as needed). Medications
whose discontinuation is uncomfortable but rarely dangerous -- SSRIs and
SNRIs such as paroxetine and venlafaxine -- are deliberately not on it.
Matching is by whole word, case-insensitive, on a generic or brand name, so
"Lithium carbonate ER 300 mg" matches ``lithium`` and "Lamictal XR" matches
``lamictal``.

Pure: no I/O. The repository gathers the rows; this module decides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from .refill_request import RefillRisk, RunOutSource

#: The DEA schedules that make a prescription a controlled substance.
CONTROLLED_SCHEDULES: frozenset[str] = frozenset({"II", "III", "IV", "V"})


@dataclass(frozen=True)
class InterruptionRiskEntry:
    """A group of medications that are harmful to interrupt, and why."""

    reason: str
    names: tuple[str, ...]


INTERRUPTION_RISK_DEFAULTS: tuple[InterruptionRiskEntry, ...] = (
    # FDA Drug Safety Communication, 2020-09-23, "FDA requiring Boxed Warning
    # updated to improve safe use of benzodiazepine drug class": "Stopping
    # them abruptly or reducing the dosage too quickly can result in
    # withdrawal reactions, including seizures, which can be life-threatening."
    # Applies to every benzodiazepine product.
    InterruptionRiskEntry(
        "dangerous withdrawal",
        (
            "alprazolam",
            "xanax",
            "clonazepam",
            "klonopin",
            "lorazepam",
            "ativan",
            "diazepam",
            "valium",
            "chlordiazepoxide",
            "librium",
            "clorazepate",
            "tranxene",
            "oxazepam",
            "temazepam",
            "restoril",
            "triazolam",
            "halcion",
            "clobazam",
            "onfi",
        ),
    ),
    # Phenobarbital tablets labeling (DailyMed set 037c7532-7688-44fd-8c76-
    # d25e8adf5fb5), Drug Abuse and Dependence: "Abrupt cessation after
    # prolonged use in a person who is dependent on the drug may result in
    # withdrawal symptoms, including delirium, convulsions, and possibly death."
    InterruptionRiskEntry("dangerous withdrawal", ("phenobarbital",)),
    # NEURONTIN labeling (NDA 020235 s057, 2014), 5.4 "Withdrawal Precipitated
    # Seizure, Status Epilepticus": "Antiepileptic drugs should not be abruptly
    # discontinued"; 2.x: reduce or stop "gradually over a minimum of 1 week".
    InterruptionRiskEntry(
        "dangerous withdrawal", ("gabapentin", "neurontin", "gralise", "horizant")
    ),
    # LYRICA labeling (NDA 021446 s046, 2025), highlights (5.4): "Abrupt or
    # rapid discontinuation may increase the risk for seizures. Withdrawal
    # symptoms or suicidal behavior and ideation have been observed after
    # discontinuation. Taper LYRICA gradually over a minimum of 1 week."
    InterruptionRiskEntry("dangerous withdrawal", ("pregabalin", "lyrica")),
    # CLOZARIL labeling (NDA 019758 s107, 2026), 5.x: orthostatic hypotension,
    # bradycardia and syncope occur "when restarting patients who have had even
    # a brief interruption in treatment"; 17: a patient who misses it "for 1
    # day or more" should not restart at the same dosage.
    InterruptionRiskEntry(
        "restart needs re-titration", ("clozapine", "clozaril", "versacloz", "fazaclo")
    ),
    # LAMICTAL labeling (NDA 020241 s068/s069, 2025), 2.1: "If a patient has
    # discontinued lamotrigine for a period of more than 5 half-lives, it is
    # recommended that initial dosing recommendations and guidelines be
    # followed"; the boxed warning ties serious rash to exceeding that escalation.
    InterruptionRiskEntry("restart needs re-titration", ("lamotrigine", "lamictal", "subvenite")),
    # Suppes T, Baldessarini RJ, Faedda GL, Tohen M. Risk of recurrence
    # following discontinuation of lithium treatment in bipolar disorder.
    # Arch Gen Psychiatry. 1991;48(12):1082-1088: over half of new episodes
    # came within about 10 weeks of stopping, mania far sooner than depression.
    InterruptionRiskEntry("relapse", ("lithium", "lithobid", "eskalith")),
    # American Psychiatric Association, Practice Guideline for the Treatment of
    # Patients With Schizophrenia, 3rd ed. (2020), recommendation 4 (1A):
    # patients whose symptoms have improved with an antipsychotic "continue to
    # be treated with an antipsychotic medication". Clozapine is listed above.
    InterruptionRiskEntry(
        "relapse",
        (
            "aripiprazole",
            "abilify",
            "brexpiprazole",
            "rexulti",
            "cariprazine",
            "vraylar",
            "haloperidol",
            "haldol",
            "olanzapine",
            "zyprexa",
            "quetiapine",
            "seroquel",
            "risperidone",
            "risperdal",
            "paliperidone",
            "invega",
            "ziprasidone",
            "geodon",
            "lurasidone",
            "latuda",
            "asenapine",
            "saphris",
            "iloperidone",
            "fanapt",
            "lumateperone",
            "caplyta",
            "chlorpromazine",
            "fluphenazine",
            "perphenazine",
            "thiothixene",
            "loxapine",
        ),
    ),
    # DEPAKOTE labeling (NDA 018723 s065, 2021), 2.x: "Antiepilepsy drugs
    # should not be abruptly discontinued in patients in whom the drug is
    # administered to prevent major seizures because of the strong possibility
    # of precipitating status epilepticus with attendant hypoxia and threat to life."
    InterruptionRiskEntry(
        "relapse", ("valproate", "valproic", "divalproex", "depakote", "depakene")
    ),
    # TEGRETOL labeling (NDA 016608 s097, 2015), Precautions: "As with all
    # antiepileptic drugs, Tegretol should be withdrawn gradually to minimize
    # the potential of increased seizure frequency."
    InterruptionRiskEntry(
        "relapse", ("carbamazepine", "tegretol", "equetro", "carbatrol", "epitol")
    ),
    # CATAPRES labeling (NDA 017407 s033, 2010), Warnings, Withdrawal: "Sudden
    # cessation of clonidine treatment has, in some cases, resulted in ... a
    # rapid rise in blood pressure".
    InterruptionRiskEntry("rebound", ("clonidine", "catapres", "kapvay")),
    # SUBOXONE labeling (NDA 022410, SPL effective 2025-12-22), 5.7 "Risk of
    # Opioid Withdrawal with Abrupt Discontinuation", and 2.2: patients
    # "have the potential for relapse, putting them at risk for opioid overdose".
    InterruptionRiskEntry(
        "opioid treatment",
        ("buprenorphine", "suboxone", "subutex", "zubsolv", "sublocade", "brixadi"),
    ),
    # Methadone hydrochloride oral concentrate labeling (DailyMed set 0955ea4a-
    # c6b8-431f-ba42-9641b5b102ea, 2026), 2.6/2.7: "Do not rapidly reduce or
    # abruptly discontinue"; "Apprise patients of the high risk of relapse to
    # illicit drug use associated with discontinuation of methadone maintenance".
    InterruptionRiskEntry("opioid treatment", ("methadone", "methadose", "dolophine")),
)

_DEFAULT_NAMES: frozenset[str] = frozenset(
    name for entry in INTERRUPTION_RISK_DEFAULTS for name in entry.names
)
_WORD = re.compile(r"[a-z]+")


def interruption_risk_by_default(medication: str) -> bool:
    """Whether *medication* (a name as written) is on the default list."""
    return any(word in _DEFAULT_NAMES for word in _WORD.findall(medication.lower()))


def is_controlled_schedule(schedule: str) -> bool:
    return schedule in CONTROLLED_SCHEDULES


def run_out_from_days_left(today: date, days_left: int) -> date:
    """The client runs out *days_left* days after *today*; 0 is today."""
    return today + timedelta(days=days_left)


def run_out_from_prescription(written_on: date, days_supply: int, refills: int) -> date:
    """The latest a prescription's supply lasts: the first fill and every refill, on time."""
    return written_on + timedelta(days=days_supply * (1 + max(0, refills)))


def same_drug(prescribed: str, medication: str) -> bool:
    """Whether a prescription names the same drug as a medication, by its first word.

    "Lithium carbonate" and "lithium carbonate ER 300 mg" are the same drug;
    a brand and its generic are not matched, which only means no estimate.
    """
    first = _WORD.findall(prescribed.lower())
    words = _WORD.findall(medication.lower())
    return bool(first) and bool(words) and first[0] == words[0]


@dataclass(frozen=True)
class PrescriptionOnFile:
    """The most recent signed prescription for the requested drug."""

    written_on: date
    schedule: str
    days_supply: int | None
    refills: int


def assess(
    *,
    medication: str,
    client_run_out: date | None,
    prescription: PrescriptionOnFile | None = None,
    clinician_controlled: bool | None = None,
    clinician_interruption_risk: bool | None = None,
) -> RefillRisk:
    """Decide the three facts for one request (see the module docstring).

    *medication* is the medication's name as the chart lists it, or as the
    client typed it when it is not on their list.
    """
    run_out: date | None = client_run_out
    source: RunOutSource | None = "client" if client_run_out is not None else None
    if run_out is None and prescription is not None and prescription.days_supply:
        run_out = run_out_from_prescription(
            prescription.written_on, prescription.days_supply, prescription.refills
        )
        source = "prescription"
    if prescription is not None:
        controlled = is_controlled_schedule(prescription.schedule)
    else:
        controlled = clinician_controlled is True
    interruption = (
        clinician_interruption_risk
        if clinician_interruption_risk is not None
        else interruption_risk_by_default(medication)
    )
    return RefillRisk(
        run_out_date=run_out,
        run_out_source=source,
        controlled=controlled,
        interruption_risk=interruption,
    )
