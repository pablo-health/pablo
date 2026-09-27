# Patient export format

A patient export is one client's chart in a form a person can read and a
script, an agent or another system can load without running Pablo. It meets a
client's right of access (45 CFR 164.524) and lets a chart move wherever it
needs to go next.

## Requesting an export

```
GET /api/patients/{patient_id}/export
```

| Parameter | Values | Default |
| --- | --- | --- |
| `format` | `zip`, `pdf` or `json` | `json` |
| `include_transcripts` | `true` or `false` | `false` |
| `include_psychotherapy_notes` | `true` or `false` | `false` |

`zip` is the archive described here. `pdf` is the chart document alone.
`json` is an earlier, unversioned shape; build against the archive's
`patient.json` instead.

`include_transcripts` adds each session's transcript. When it is off, a
session has no `transcript` key, so an omitted transcript never reads as an
empty one. `include_psychotherapy_notes` adds the requesting clinician's own
psychotherapy notes, which the right of access does not reach. Both choices
are recorded in `options` and on the export's audit log entry.

## What the archive holds

| File | Contents |
| --- | --- |
| `chart.pdf` | The chart as a document to read or print. |
| `patient.json` | The same chart as structured data. |
| `schema.json` | The JSON Schema that `patient.json` follows. |
| `documents/<id>__<filename>` | Each file uploaded to the chart, as it was uploaded. |
| `intake/<assignment_id>.html` | Each submitted intake form, as a document to read or print. |
| `manifest.json` | Every other file, with its size and SHA-256 checksum. |
| `README.txt` | Which file is which, the schema version, and a pointer to this page. |

`patient.json` lists every file under `documents/` in `documents`, with its
category, uploaded filename, content type, size, SHA-256, upload time, who
uploaded it (`clinician` or `patient`) and its path in the archive.
Psychotherapy-notes documents are included only with
`include_psychotherapy_notes`. A clinician's private working files are not
part of the record and are never included. In `manifest.json`, uploaded
files have kind `document` and intake forms kind `intake_form`.

## What `patient.json` holds

| Key | Contents |
| --- | --- |
| `patient`, `practitioner` | The client's demographics, and who the record comes from. |
| `sessions` | Each session, with its note and, when asked for, its transcript. |
| `standalone_notes` | Notes written without a session, such as an intake, a treatment plan or a safety plan. |
| `documents` | Each uploaded file, with where it is in the archive and its checksum. |
| `appointments` | The schedule, cancelled appointments included. |
| `outcome_measures` | Each scored instrument, such as a PHQ-9, with its item responses. |
| `message_threads` | Secure messages with the client. An attachment is named by its id in `documents`. |
| `medications` | The medication list. |
| `diagnoses` | Each diagnostic assessment, with the ICD-10-CM code the clinician confirmed. |

`chart.pdf` has a section for each of these. Conversations with the
assistant are not part of the record and are not exported.

Object names follow FHIR where that costs nothing: a session is an
`Encounter`, a note a `DocumentReference`, an instrument result an
`Observation`, a medication a `MedicationStatement`, a diagnosis a
`Condition`. Keys are snake_case and every timestamp is ISO 8601 with an
offset.

## The schema

The published schema is [export-schema.json](export-schema.json), generated
from the export models. CI fails when the two disagree, so it matches the
`schema.json` an archive ships. Regenerate it with `make export-schema`.

## Versioning

`schema_version` in `patient.json` and `manifest.json` names the version an
archive was written in. The current version is `1.2`.

| Version | Change |
| --- | --- |
| `1.2` | Adds `appointments`, `outcome_measures`, `message_threads`, `medications` and `diagnoses`, and a `chart.pdf` section for each. |
| `1.1` | Adds `documents` and the `documents/` and `intake/` folders. |
| `1.0` | The first published version. |

- **Minor** (`1.0` to `1.1`): an additive change, such as a new field or
  file. A consumer written for `1.0` still reads a `1.1` archive.
- **Major** (`1.x` to `2.0`): a rename, a removal or a change of meaning.
  Check the major version before reading anything else.

Billing and CSV files are planned for the archive. Each arrives as a minor
version that adds fields and files without changing the ones described here.
