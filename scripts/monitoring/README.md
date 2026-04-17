# Pablo Monitoring

GCP-native observability for self-hosted Pablo. Covered by your existing
GCP BAA, no third-party accounts required, stays within Cloud Monitoring's
generous free tier for a solo practice.

## What it sets up

- Cloud Monitoring uptime checks (1-min interval, multi-region) on backend
  `/healthz` and frontend `/`
- Alert policies:
  - Backend 5xx rate > 1% over 5 min
  - Cloud SQL disk utilization > 80%
  - Uptime check failure
  - **HIPAA log-review / pentest HIGH findings** — log-based alert
    that fires when either Cloud Run Job emits a HIGH-severity result
- Email notification channel routed to your chosen address (all alert
  policies above use it)

## Requires

- The GCP project Pablo is deployed in
- An email address for alerts

## Run

```bash
./scripts/monitoring/setup.sh \
  <project-id> <backend-url> <frontend-url> <alerts-email>
```

The script is idempotent — safe to re-run. It will detect existing
resources by display name and reuse them.

## What's covered vs. not

Covered by the Simple tier:
- Uptime / availability
- Server-side error rate
- Database health (disk pressure)
- Basic paging via email

Not covered (consider adding later):
- Distributed tracing / APM
- Frontend error tracking (client-side exceptions)
- Synthetic multi-step user-journey checks
- SLO burn rate alerts
- On-call rotation / escalation

If you want any of those, Pablo is AGPL — you can layer New Relic,
Sentry, DataDog, Grafana Cloud, or whatever suits by following the
existing script as a template. The backend image already contains
env-var-gated hooks for New Relic APM and Sentry (set
`NEW_RELIC_LICENSE_KEY` or `SENTRY_DSN` and redeploy — no code change).

## HIPAA BAA posture

Cloud Monitoring and Cloud Logging are covered under your Google Cloud
BAA. Nothing in this tier forwards data outside GCP. If you extend with
third-party tools, make sure either (a) you hold a BAA with that vendor
or (b) you only forward PHI-free metadata and scrub PII in the SDK
(e.g., Sentry's `beforeSend` + `sendDefaultPii: false`).
