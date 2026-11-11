# Pablo

**Open-source practice software for therapists, with notes drafted from the session.**

Pablo drafts your note from the session, in SOAP, DAP, BIRP, GIRP or a format you define, and links each sentence to the part of the session it came from so you can check it before you sign. Scheduling, intake, the client portal, insurance claims and payments are in the same app. Run it on your own Google Cloud account with one script.

## Features

### Before a session

- **Scheduling and online booking** — Set your hours and share a booking link. Google Calendar and iCal sync bring in appointments you keep elsewhere.
- **Client portal** — Clients book, fill in intake forms, sign consents and message you.
- **Intake forms you build** — Choose the documents and questions. Answers and signed consents land on the chart.
- **Insurance on file** — Your payer list, each client's plan, and eligibility checks against the payer.

### In the session

- **Recording with Pablo Companion** — The [desktop app](https://github.com/pablo-health/pablo-companion) for Mac and Windows records the session, with your client's consent, and sends it to your Pablo for the note.
- **Notes drafted for you** — SOAP, DAP, BIRP, GIRP, intake, safety plan, treatment plan, or note types your practice defines. Every sentence links back to the transcript. No recording? Upload or paste a transcript, or write the note yourself.

### After the session

- **The chart keeps up** — When the session changes something on the chart, such as a medication or an allergy, Pablo suggests the update and shows the lines that support it. You accept or skip each one.
- **Ask about a client** — Pablo answers from the chart and shows the documents it used.
- **Outcome measures** — PHQ-9, GAD-7 and others, trended on the chart.
- **Insurance claims** — Build, check and submit claims through a clearinghouse, and track what the payer paid.
- **Credentialing checklist** — Keep the details payers ask for in one place and track each application.
- **Payments and superbills** — Charge a card on file with your own Stripe account, and download superbills and statements.
- **One inbox** — Unsigned notes, portal messages, refill requests and intake submissions in one list.

### Around the practice

- **Moving in** — Import a SimplePractice export: client list, calendar and notes.
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

The whole path, from a Google account to your first recorded session, is in
[Start here](docs/self-hosting/start-here.md).

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

**Pablo One** is the same app, run for you: $59 per clinician a month at
the founding rate ($79 list). It adds the parts that need us — our servers,
our vendor contracts, our BAA, and a person:

- **An AI front office.** Pablo sorts your practice email, drafts the routine
  replies from your fees, hours and insurance, and puts possible safety
  messages at the top for you. You choose, topic by topic, whether Pablo files
  it, drafts a reply for you to send, or sends it.
- **Intake packets built with you.** Answer a few questions about your
  practice; Pablo lists the documents your setup calls for and drafts the forms.
- **A practice website, with SEO.** Written in your own words, published on
  your domain, registered with Google and Bing, and kept current.
- **Insurance, ready to bill.** Claims at the clearinghouse's cost, eligibility
  checked automatically at intake, and help preparing and tracking your
  credentialing applications under your own NPI.
- **Appointment reminders**, sent for you.
- **A HIPAA-eligible mailbox**, and the BAA signed at signup.
- **We run it.** Updates, backups and monitoring.
- **A person.** Kurt sets up each founding practice with you, including the
  move from SimplePractice, and you talk to the developer directly.

Try it for 30 days; if it is not a fit, you get a full refund.

Self-hosting costs about $20 a month and keeps everything in your own Google
Cloud account; Pablo One is for when you would rather not run a server.
Learn more at [pablo.health](https://pablo.health).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

[AGPL-3.0](LICENSE) — You can self-host and modify Pablo freely. If you host a modified version as a service, you must release your changes under the same license.

## Registration

If you deploy Pablo for clinical use, please email support@pablo.health so we can notify you of critical security updates. This is a condition of using Pablo in production.
