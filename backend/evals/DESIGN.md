# Pablo evals — program design

> README is "how to run." This doc is "why it looks the way it does."

Authored 2026-05-23 during the THERAPY-rb9z eval-program rebuild. If
this doc and the code disagree, the code is right; update the doc.

---

## §1. Why evals exist for pablo

Pablo's `StructuredSOAPNoteModel`
(`backend/app/models/soap_note.py`) ships every generated claim with
`source_segment_ids` pointing back to the transcript turns it came
from. The *architecturally-correct* hallucination check is structural:
every claim has a non-empty `source_segment_ids`, the cited segments
exist, and the cited segments support the claim.

That check is not shipped today. `_run_source_attribution`
(`backend/app/services/note_generation_service.py:242`) runs a second
LLM call every SOAP generation to populate `source_segment_ids`, but
the verification layer that would *check* attribution
(`SourceVerificationService`, embedding + NLI agreement) is gated
behind `ENABLE_EMBEDDING_VERIFICATION` and disabled by default.
Failures in attribution are explicitly swallowed: "the SOAP note
remains valid without sources." Net: the field gets populated, the
populated values aren't trusted, and the catch-layer is off.

So evals exist to do, probabilistically and from the outside, the job
provenance was supposed to do structurally and cheaply. The eval
program is the substitute for trustworthy per-claim grounding. If
provenance ever becomes reliable, this program partly converts from
front-line hallucination check to drift/regression monitoring — both
remain valuable, but the priority order flips.

This framing matters because it sets the load. Today, evals + therapist
review are the only two things standing between Pablo and a
confidently-hallucinated SOAP note in a patient's chart. Build
accordingly.

---

## §2. Two-tier structure

There are exactly two layers of scoring. There is no middle layer.
(In an earlier draft I proposed a structural provenance-grounding
layer between regex and LLM-judge — that layer doesn't exist because
the prerequisite feature is shelved. Don't reintroduce it without
also re-enabling provenance verification.)

### Tier 1 — Deterministic (regex / keyword / structure)

- Runs in CI on every PR. Free to run. Cheap to author.
- Catches loud failures: missing sections, format breakage, named
  forbidden inventions, surface facts that the transcript states
  explicitly.
- Lives in `backend/evals/scorers/` as functions matching the
  Braintrust scorer signature (see §5).

Tier-1 scope:

- **Format adherence** — section presence + ordering across SOAP,
  DAP, BIRP, GIRP, Narrative. Non-empty per section. Valid JSON
  shape if the case carries `template:` other than Narrative.
- **Forbidden-invention blocklists** — `forbidden_inventions: [list]`
  on the case is enforced by word-boundary regex on the generated
  text.
- **Surface-fact mention checks** — for facts the transcript states
  explicitly (e.g. "patient said panic attack at the grocery store").
  Permissive synonym lists.
- **Negation discipline** — patient denied X → note can't claim
  active X. Pattern-detect on denied items.

Tier 1 is NOT for: judgment calls, semantic equivalence, "did the
note miss anything important," "is the plan reasonable." Those
belong in Tier 2.

### Tier 2 — LLM-judge (semantic)

- Runs on a schedule, not per-PR. Costs real $.
- Catches subtle hallucination, completeness gaps, judgment-call
  faithfulness.
- Consumes the reference SOAP sidecars (`*.soap.txt` files alongside
  full-length transcripts) as ground-truth input to the judge prompt.
- One scorer entry point today:
  `backend/evals/scorers/llm_judge_faithfulness.py`.

Tier-2 scope:

- "Did the generated SOAP miss anything important the transcript
  contains?"
- "Did the generated SOAP assert anything not supportable from the
  transcript?"
- Judgment-call assertions ("captures the ambivalence without
  resolving it", "doesn't pathologize the patient's occupation",
  "uses appropriate diagnostic hedging at intake-1").
- Optional: semantic-equivalence comparison against the reference
  SOAP.

Tier 2 is NOT for: things Tier 1 already catches (don't pay LLM cost
to re-detect blocklisted inventions). Tier 2 results are advisory
inputs to the launch-bar conversation; they don't block PRs.

---

## §3. The 95% rule

Only require the generated note to assert something the transcript
makes inevitable. If a human reviewer would disagree about whether
the assertion is warranted, the assertion does not belong in a
Tier-1 `must_capture_*` field. Aspirational assertions belong in
Tier 2 judge prompts.

Examples where the rule holds (fair Tier-1 contracts):

- Patient describes anhedonia + insomnia + concentration issues +
  4-month duration → note must use a depressive-spectrum term.
- Patient explicitly denies plan and intent → note must not state
  active SI risk.
- Patient was in a MVA 5 months ago with intrusive symptoms +
  avoidance → note must include a stress-related differential.

Examples where the rule does NOT hold (Tier-2 only):

- "Captures the ambivalence without resolving it" — judgment call,
  no regex can verify.
- "Treatment plan is reasonable" — multiple valid plans.
- "MSE language is appropriate" — depends on inferences not in the
  transcript.
- "Uses ICD-10 code Z63.0" — overspecific; transcript doesn't make
  the code inevitable.

Practical test: if the assertion needs a clinician's eye to verify,
it belongs in Tier 2.

---

## §4. Case shape

Every case in `datasets/note_generation.yaml` carries a `tier` field:

```yaml
- id: note-faith-XXX
  surface: note_generation
  category: faithfulness | format_adherence
  tier: 1 | 2
  description: <one-line summary>
  input:
    template: SOAP | DAP | BIRP | GIRP | Narrative
    provider_type: therapist | prescriber | both
    transcript: |              # OR
      ...inline transcript...
    transcript_path: transcripts/whatever.txt  # resolved to input.transcript at load time
  expected:
    # Tier-1-only fields:
    must_have_sections: [...]
    sections_in_order: true
    forbidden_inventions: [...]
    must_capture_*: ...
    must_not_*: ...
    # Tier-2-only fields:
    reference_soap_path: transcripts/whatever.soap.txt
    judge_directives:          # free-text directives for the judge prompt
      - "Does the note treat the bipolar diagnosis as a rule-out, not confirmed?"
      - "Does the assessment avoid pathologizing the patient's occupation?"
```

Mixing Tier-1 and Tier-2 fields on a single case is allowed when the
case is genuinely a hybrid — but the scorers gate on which fields are
present, so a case missing `judge_directives` isn't run through the
judge, and a case missing `forbidden_inventions` isn't run through
that scorer. `{"score": None}` from a scorer means "not applicable to
this case."

The dropped `EXPECTED_CASE_COUNT` constant in
`test_note_generation.py`: gone. Counting cases isn't a meaningful
test invariant; checking the YAML parses + categories add up is. Use
positive assertions on what each tier requires (every Tier-2 case
has a `reference_soap_path` or `judge_directives`, etc.).

---

## §5. Scorer contracts

A scorer is a function with this signature
(see `backend/evals/scorers/instruction_holding.py:46` for the
existing pattern):

```python
def my_scorer(
    *,
    output: str,
    expected: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    if not <case-applicable>:
        return {"score": None}  # Braintrust skips
    return {
        "name": "my_scorer",
        "score": <float in [0, 1]>,
        "metadata": {...},
    }
```

`output` is the generated note. For SOAP it's typically the
`SOAPNoteModel`'s four-string-blob JSON (`{"subjective": "...",
"objective": "...", "assessment": "...", "plan": "..."}`); the scorer
either parses the JSON or operates on the string blob — the existing
no-confabulation scorer goes the string-blob route, which is fine
for Tier 1.

The LLM-judge scorer at
`backend/evals/scorers/llm_judge_faithfulness.py` takes the same
shape externally; internally it makes an LLM call and parses
structured output (`pass: bool`, `missing_facts: list`,
`hallucinated_facts: list`).

New scorers register in `backend/evals/scorers/__init__.py`'s
`__all__`. Runner scripts (`run_chat_experiment.py` etc.) import
them explicitly and pass them in the `scorers=[...]` list to the
Braintrust `Eval()` call.

---

## §6. Synthetic data rules

The original rule was "synthetic from scratch; never anonymize a
real transcript." That was over-conservative — it conflated HIPAA
(which is about PHI from covered entities) with the broader
re-identification risk.

Corrected framing:

1. **PHI is off-limits.** No real clinical session content. No
   anonymized real session content (anonymization ≠ redaction;
   re-identification is realistic from transcript content alone).
2. **Material the original author published publicly under a
   redistribution-permitting license is NOT PHI** — the author
   vouched for the provenance when they posted it. Such material is
   usable in our datasets.
3. **License still matters.** Public ≠ redistributable. Verify the
   license permits use in this AGPL-3.0 repo.
4. **Synthetic-from-scratch is still the simplest path** — no
   license question, no provenance question. Default to it when the
   marginal authoring cost is low.

Examples under the corrected framing:

- ✅ The `synthetic_therapy_data/session_00{2,3,4}` and `session_001`
  sets from the predecessor repos (`meeting-transcription`,
  `kurt-meeting-bot`) — authors' README explicitly attests
  "completely fictional." Public on disk, redistributable, fine.
  Name-swap is sufficient (the original names are too plausibly-real
  for our convention but the content isn't PHI).
- ✅ AnnoMI (133 real MI transcripts from public YouTube/Vimeo demos,
  CC-BY-4.0) — participants consented to public posting; not PHI;
  license permits use.
- ❌ DAIC-WOZ (189 depression interviews) — academic-only license
  blocks redistribution, even though the content is consent-cleared.
- ❌ Any anonymized real session that wasn't published publicly with
  consent. Re-identification risk remains; we won't be the ones
  carrying it.

Naming convention for synthetic personas: alliterative-fictional or
clearly-invented (Bayer Mountain, Imaginary Ingles, Pseudo Pendleton,
Apocryphal Atwood). Avoid plausibly-real names (Jade Morrison, Dr.
Lisa Chen) even in obviously-synthetic content — review pattern
consistency matters more than the strict no-PHI threshold here.

---

## §7. What's deferred

In rough priority order:

1. **Wiring scorers to Braintrust scheduled experiments.** The
   LLM-judge needs to run somewhere on a cadence; that "somewhere"
   is unbuilt. Until it exists, Tier-2 runs ad-hoc via the spike
   runner.
2. **Threshold tuning.** Right now `pass: bool` is judge-decided per
   case. A program-wide pass-rate threshold (e.g. "ship when ≥80% of
   full-length Tier-2 cases pass") is a launch-bar decision deferred
   to closer to launch.
3. **Multi-judge ensembling.** Single judge per case today. Worth
   considering if any one judge model is biased; not worth building
   before there's evidence of bias.
4. **Segment-level NLI scoring.** This is the missing structural
   middle layer (§2). Becomes available when provenance verification
   gets re-enabled in `note_generation_service`. Will be the highest-
   leverage scorer in the program when it exists. Not on the
   pre-launch path.
5. **Cross-section consistency checks.** "Assessment references
   symptoms from Subjective; Plan references interventions from
   Assessment." Worth doing; Tier-2 territory; not in scope today.
6. **Coverage gaps** — GAD, postpartum, adult-survivor of childhood
   abuse, IPV, additional note formats. Tracked under THERAPY-wksg,
   THERAPY-7l1q, THERAPY-blcy, THERAPY-b86c.

If anything in this deferred list becomes load-bearing for launch,
update §7 and file a bead. Don't silently expand scope.
