# Pablo

**AI-powered therapy documentation that eliminates clinician paperwork.**

Pablo generates SOAP notes from session transcripts with dual-method verification (LLM + classical NLP), ensuring zero hallucinations and full source attribution. Self-host on your own GCP account with one script.

## Features

- **AI SOAP Note Generation** — Upload a session transcript, get a clinically accurate SOAP note
- **Built-in Calendar** — Schedule and manage therapy sessions
- **Patient Management** — Secure patient records with session history
- **Built for HIPAA** — HTTPS only, mandatory MFA, a 15-minute idle logoff, an append-only audit log of every record access, continuous backups with a monthly restore check. What the installer sets up and what stays yours: [Self-Hosting HIPAA Guide](docs/SELF_HOSTING_HIPAA_GUIDE.md)

## Quick Start

Pablo runs on one small server in your own Google Cloud account, for about
$20 a month billed by Google. Your records stay in your account.

### Before you start

- A Google Cloud billing account ([add one](https://console.cloud.google.com/billing/create))
- Google's Business Associate Agreement, accepted by an administrator of your
  account ([how](https://cloud.google.com/security/compliance/hipaa))

### Install

[![Open in Cloud Shell](https://gstatic.com/cloudssh/images/open-btn.svg)](https://shell.cloud.google.com/cloudshell/editor?cloudshell_git_repo=https://github.com/pablo-health/pablo.git&cloudshell_tutorial=deploy/vm/TUTORIAL.md)

Cloud Shell opens with a short guide beside the terminal. Run:

```bash
./install.sh
```

It asks a few questions (your email, and whether you want Google sign-in or
transcription now), shows what it will set up, and installs in about 15
minutes: the app, a Postgres database on its own disk with daily backups to
Cloud Storage, HTTPS, and sign-in with mandatory MFA. No console steps are
needed for email and password sign-in.

Prefer your own terminal? With the [gcloud CLI](https://cloud.google.com/sdk/docs/install):

```bash
git clone https://github.com/pablo-health/pablo.git
cd pablo
./install.sh
```

Updates, backups, costs and options: [deploy/vm/README.md](deploy/vm/README.md).

### Try it on your own machine

`./install.sh --target local` runs Pablo in Docker on a Mac, or on Windows
with WSL or Linux, with a public address from Tailscale Funnel and nightly
backups to your Google Cloud account. Good for trying Pablo out; running a
practice on it is possible if you take on what that means. Requirements and
risks: [deploy/workstation/README.md](deploy/workstation/README.md).

### Local Development

```bash
# Database: start Postgres, then build and provision the schemas
make db-up db-migrate

# Backend
cd backend && poetry install && poetry run uvicorn app.main:app --reload

# Frontend
cd frontend && npm ci && npm run dev

# Or use Docker (brings up Postgres, runs the migrate step, then the backend;
# run the frontend separately)
docker compose up
```

## Architecture

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 16, TypeScript, Tailwind CSS, shadcn/ui |
| Backend | FastAPI, Python 3.13, PostgreSQL (Cloud SQL) |
| AI | Google Gemini (via Vertex AI, default) or Anthropic Claude |
| Infra | Google Cloud Run, pre-built images on `ghcr.io/pablo-health` |

## HIPAA Compliance

Self-hosting Pablo means **you** are the covered entity. The [Self-Hosting HIPAA Guide](docs/SELF_HOSTING_HIPAA_GUIDE.md) says what `install.sh` builds, what it does not (no alerting, no scheduled purge, audit retention set but not locked), and a shared-responsibility matrix for the rest.

Two things worth flagging up front for self-hosters:

1. **Sign the Google Cloud BAA** before you deploy. It's free, takes a few minutes, and covers Compute Engine / Cloud Storage / Secret Manager / Identity Platform / Vertex AI. Console → Settings → Compliance → Business Associate Agreement.
2. **Pick your transcription provider and its BAA.** `assemblyai` is the only transcription backend wired for deployment today — it's lower ops, but requires a direct BAA with AssemblyAI before any PHI is sent. A self-hosted `whisper` path (Cloud Batch + spot GPUs, covered by the Google Cloud BAA and needing no third-party BAA) is planned but not yet operational. `install.sh` asks whether to turn on transcription; saying yes is your statement that the AssemblyAI BAA is signed. Otherwise transcription stays off. Details in the [Self-Hosting HIPAA Guide](docs/SELF_HOSTING_HIPAA_GUIDE.md#business-associate-agreements).

## Want Managed Hosting?

**Pablo One** is the same engine, run for you: $59 per clinician a month at
the founding rate ($79 list). On top of what you get by self-hosting:

- We run it: updates, backups and monitoring, with the BAA signed at signup
- Insurance credentialing done for you: two payers included for founding
  practices, then at cost
- Insurance claims at cost, and eligibility checks that run automatically
- A practice website, built for you
- A HIPAA-eligible mailbox

Self-hosting costs about $20 a month and keeps everything in your own Google
Cloud account; Pablo One is for when you would rather not run a server.
Learn more at [pablo.health](https://pablo.health).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

[AGPL-3.0](LICENSE) — You can self-host and modify Pablo freely. If you host a modified version as a service, you must release your changes under the same license.

## Registration

If you deploy Pablo for clinical use, please email support@pablo.health so we can notify you of critical security updates. This is a condition of using Pablo in production.
