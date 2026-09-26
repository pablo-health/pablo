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
| `manifest.json` | Every other file, with its size and SHA-256 checksum. |
| `README.txt` | Which file is which, and a pointer to this page. |

Object names follow FHIR where that costs nothing: a session is an
`Encounter`, a note a `DocumentReference`. Keys are snake_case and every
timestamp is ISO 8601 with an offset.

## The schema

The published schema is [export-schema.json](export-schema.json), generated
from the export models. CI fails when the two disagree, so it matches the
`schema.json` an archive ships. Regenerate it with `make export-schema`.

## Versioning

`schema_version` in `patient.json` and `manifest.json` names the version an
archive was written in. The current version is `1.0`.

- **Minor** (`1.0` to `1.1`): an additive change, such as a new field or
  file. A consumer written for `1.0` still reads a `1.1` archive.
- **Major** (`1.x` to `2.0`): a rename, a removal or a change of meaning.
  Check the major version before reading anything else.

Documents, clinical data, billing and CSV files are planned for the archive.
Each arrives as a minor version that adds fields and files without changing
the ones described here.
