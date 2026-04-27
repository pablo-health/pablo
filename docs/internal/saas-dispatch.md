# SaaS Dispatch — `oss-deployed` event

When the OSS `Deploy` workflow finishes a successful production deploy on
`main`, the final `notify-saas` job emits a `repository_dispatch` event of
type `oss-deployed` to `pablo-health/pablo-saas`. The SaaS-side
`base-bump.yml` workflow listens for that event, bumps `BASE_IMAGE.txt`
to the new SHA, opens a PR, and auto-merges it. End-to-end propagation
typically lands on SaaS `main` within a few minutes of OSS deploy
completion (vs. the ≤7 day weekly-cron lag this replaces).

## Failure isolation

The dispatch step is `continue-on-error: true`. A token expiry, GitHub
API outage, or permission misconfiguration on the SaaS side will surface
as a yellow check on the OSS deploy run but will **not** mark the deploy
itself as failed. The weekly cron in `pablo-saas/base-bump.yml` is the
backstop.

## `SAAS_DISPATCH_TOKEN`

A fine-grained personal access token, stored as a repo secret on
`pablo-health/pablo` named exactly `SAAS_DISPATCH_TOKEN`.

Required scopes:

| Repo | Permission |
|------|------------|
| `pablo-health/pablo` | `contents: read` |
| `pablo-health/pablo-saas` | `contents: write` |
| `pablo-health/pablo-saas` | `actions: write` |

`actions: write` is what `repository_dispatch` requires on the target
repo; `contents: write` lets the SaaS workflow push the bump branch and
auto-merge the PR.

The token is owned by a human (currently the repo admin) — if/when it
rotates, update the secret in `pablo-health/pablo` → Settings → Secrets
and variables → Actions.

## Manually triggering a dispatch

Useful for testing the SaaS listener after changes, or for forcing a
rebuild without an OSS deploy:

```bash
gh api -X POST repos/pablo-health/pablo-saas/dispatches \
  -f event_type=oss-deployed \
  -F client_payload[sha]=<some-sha-on-pablo-main>
```

The token used by `gh` needs the same `actions: write` scope on
`pablo-saas` that `SAAS_DISPATCH_TOKEN` has.

## Cross-references

- OSS workflow job: `.github/workflows/deploy.yml` → `notify-saas`
- SaaS listener: `pablo-health/pablo-saas/.github/workflows/base-bump.yml`
  (landed in pablo_saas commit `5d0bd83`, PR #11). Prefers
  `github.event.client_payload.sha` over `gh api repos/pablo-health/pablo/commits/main`
  when triggered by `repository_dispatch`.
- SaaS-side companion doc (if present):
  `pablo-health/pablo-saas/docs/internal/auto-base-bump.md`
