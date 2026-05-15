# Pablo LLM Evals

Synthetic eval harness for the chat and note-generation surfaces. Runs
against a configured Braintrust workspace.

---

## 🚨 The no-real-PHI rule (load-bearing)

**Eval datasets in this directory MUST NEVER contain real patient data,
ever, under any circumstance.** Braintrust is a hosted SaaS — there is
no BAA. Real PHI in datasets is a HIPAA violation and a contract
violation with patients.

Rules:

- Fictional patient names only. Use clearly-fake names like `Bayer
  Mountain`, `Dr. Sample`, `Pat Anonymous`. Avoid `John Smith` —
  ambiguous.
- Synthetic DOBs only. Use 1900–1920 era dates so they are obviously
  not living patients.
- Fictional addresses only (`123 Test St, Faketown, AA 00000`).
- Synthetic transcripts must be authored from scratch. Do not
  anonymize a real transcript — anonymization is not redaction; a
  hostile reader can re-identify.
- Synthetic note text follows the same rule.

A pre-commit hook (THERAPY-wjqb) will block real-looking patient data
in `datasets/`. The hook is defense-in-depth; the author is the first
line of defense.

If you are unsure whether a case is synthetic enough, **do not commit
it**. Ask the dataset owner.

---

## Quickstart

```bash
# One-time setup
cd backend/evals
cp .env.example .env
# Open .env and paste your Braintrust API key + project name

# Install the optional eval dependency group
poetry install --with evals

# Run the smoke test
poetry run pytest backend/evals/test_smoke.py -v
```

If everything is wired correctly, you should see:

- A `starter-smoke` **dataset** in the Braintrust Datasets tab
- A `scaffolding-smoke-<uuid>` **experiment** in the Experiments tab,
  scored against that dataset

---

## Directory structure

```
backend/evals/
├── __init__.py
├── README.md              # this file
├── .env.example           # template; copy to .env (gitignored)
├── conftest.py            # pytest setup — loads .env
├── harness.py             # core: push_dataset, make_model_task, run_eval
├── test_smoke.py          # pytest entry for the 5-case smoke test
├── datasets/
│   ├── __init__.py
│   └── starter_smoke.yaml # 5 placeholder cases
└── scorers/
    └── __init__.py        # real scorers land in THERAPY-j39e (Phase 1.4)
```

---

## Configuration (`.env`)

| Var | Required | Purpose |
|---|---|---|
| `BRAINTRUST_API_KEY` | yes | API key with write scope on the target workspace |
| `BRAINTRUST_PROJECT` | yes | Project name to push datasets and experiments to |
| `BRAINTRUST_DEFAULT_MODEL` | no | Default model for the smoke test. Defaults to `publishers/google/models/gemini-2.5-flash` |

`.env` is gitignored. The harness reads it via `conftest.py`; you do
not need to export the vars in your shell.

---

## Authoring eval cases

YAML schema (see `datasets/starter_smoke.yaml` for examples):

```yaml
- id: <surface>-<category>-<NNN>
  surface: chat | note_generation
  category: scope_refusal | hallucination | prompt_injection | format | faithfulness | template_selection
  description: One-line human description of what this tests.
  input:
    # Surface-specific — see below
  expected:
    # Properties the response must satisfy (scorer-checked, not exact match)
```

Chat case input:
```yaml
input:
  system: "...system prompt..."
  context: "...bundled patient context..."
  user_message: "...the actual user turn..."
```

Note-generation case input:
```yaml
input:
  template: SOAP | DAP | BIRP | ...
  transcript: "...synthetic session transcript..."
  provider_type: therapist | pmhnp
```

---

## Adding a new dataset

1. Create `datasets/<surface>_<purpose>.yaml`.
2. Author cases following the synthetic-data rules above.
3. Add a pytest entry in `test_<surface>.py` (see `test_smoke.py`
   for the pattern). The harness's `push_dataset()` will register
   the dataset as a first-class object in the Braintrust UI.
4. Run the test to push.

Real chat + note-gen datasets are filed under THERAPY-exba (Phase 1.3).

### Case id discipline

- **IDs are stable, opaque tokens. They are append-only and never
  reused.** If you delete a case from YAML, leave the id gap — past
  experiments reference records by id, and reusing an id silently
  changes what those historical scores were measured against.
- Allocate new ids by picking the next unused number in the surface
  + category namespace (e.g. if `smoke-chat-003` is the highest,
  the next is `smoke-chat-004` — even if `smoke-chat-002` was
  deleted).
- Call `push_dataset(..., sync=True)` to make the YAML canonical:
  records in Braintrust whose id is not in the YAML get deleted on
  the next push. The smoke test uses `sync=True`; Phase 1.3 datasets
  should too.

---

## When this breaks

- **`BRAINTRUST_API_KEY not set` / `BRAINTRUST_PROJECT not set`**:
  copy `.env.example` to `.env` and fill in both values.
- **`Project not found`**: Braintrust auto-creates projects on first
  push. If it's not creating, check that the API key has write scope
  on the workspace.
- **`No API keys found (for <model>)`**: the Braintrust AI proxy needs
  the model's provider configured under **Settings → Secrets** (this
  is separate from Loop / Playground AI Provider settings). For
  Vertex AI models, the model identifier must use the full publisher
  path, e.g. `publishers/google/models/gemini-2.5-flash`, not the
  short name.
- **Smoke test passes locally but is not running in CI**: a CI
  workflow lands in THERAPY-824b (Phase 1.5). Until then, evals are
  local-only.
