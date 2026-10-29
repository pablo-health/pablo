# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Words any clinical note uses, which say nothing about one client.

The copied-text check on a derived note type looks for text specific to a
sample: names, dates, identifiers, quoted speech, distinctive phrases. A
heading such as "Mental Status Exam" or a hint about "risk of harm" shares
words with every note ever written; flagging it would strip the very
structure the samples were given to show. Words listed here never count
toward a copy, alone or in a shared run.

Matching is on lowercased word tokens, so plurals and common variants are
listed alongside their stems.
"""

from __future__ import annotations

_COMMON = """
a about above after again against all also am an and any are as at be been
before being below between both but by can could did do does doing down
during each either every few for from further had has have having he her
here hers him his how i if in into is it its itself just least less like
may me might more most much must my no nor not now of off on once only or
other our out over own per same she should since so some such than that
the their them then there these they this those through to too under until
up upon us very via was we well were what when where whether which while
who whom why will with within without would yes yet you your
one two three four five six seven eight nine ten first second last next
new old today week weeks month months year years day days time times
any each including include includes included etc eg ie e g i e
note notes noted noting record recorded records document documented
documentation write written writes section sections field fields item items
list lists describe described describes description summary summarize
brief short concise detail details detailed kind type types example examples
what goes here content general specific relevant applicable none other
other's another state stated states statement statements report reported
reports reporting per as needed prn
"""

_CLINICAL = """
client clients patient patients pt individual person family caregiver
guardian parent parents spouse partner child children
clinician provider providers therapist counselor psychiatrist prescriber
physician nurse practitioner np pa md do lcsw lpc lmft psyd phd
visit visits session sessions encounter appointment appointments follow
followup follow-up return returning interval since prior previous current
date dates service services place location locations start end duration
minute minutes min mins hour hours length total
telehealth telemedicine video audio phone telephone virtual in-person office
home clinic remote consent consented attestation attest attests verbal
identity verified identified emergency contact address
cpt icd code codes coding billing bill billed modifier add-on addon e m em
evaluation management level complexity moderate low high straightforward
decision decisions making mdm problem problems data risk risks
chief complaint complaints cc hpi history histories present illness
subjective objective assessment assessments plan plans data behavior
behaviors intervention interventions response responses goal goals girp
birp dap soap progress outcome outcomes
mental status exam examination mse appearance attire grooming hygiene
behavior eye contact psychomotor speech rate rhythm volume tone mood affect
congruent reactive restricted blunted flat labile euthymic dysphoric
anxious irritable thought thoughts process content perception perceptions
hallucinations delusions orientation oriented alert cognition memory
attention concentration insight judgment judgement impulse control
suicidal homicidal ideation ideations intent plan means self-harm harm
safety protective factors safety-plan lethal access firearms denies denied
endorses endorsed screening screen screened scale scales score scores
phq gad pcl audit dast cssrs c-ssrs columbia
diagnosis diagnoses diagnostic dx differential formulation impression
impressions disorder disorders condition conditions symptom symptoms
severity severe mild functioning function functional impairment
medication medications med meds dose doses dosage dosing frequency
prescription prescriptions prescribed refill refills pharmacy adherence
compliance side effect effects adverse allergy allergies allergic nkda
tolerability tolerated titrate titration increase decrease continue
continued discontinue discontinued start started change changes adjusted
lab labs laboratory vitals vital signs blood pressure weight height bmi
pulse heart temperature ekg ecg
medical psychiatric psychosocial social substance substances alcohol
tobacco nicotine cannabis drug drugs use uses abuse misuse sleep appetite
energy stress stressors relationships work school employment housing legal
trauma developmental education past family
psychotherapy therapy therapeutic counseling supportive cognitive
behavioral cbt dbt act emdr motivational interviewing mindfulness
psychoeducation skills coping homework practice strategies techniques
technique modality modalities treatment treatments referral referrals
coordination collaborate care team education instructions recommendations
recommended return precautions monitoring monitor pdmp prescription-drug
program check checked review reviewed reviewing discussed discuss
signature signed sign electronically credentials
"""

CLINICAL_VOCABULARY: frozenset[str] = frozenset((_COMMON + _CLINICAL).split())
