# Pablo

**AI-powered therapy documentation that eliminates clinician paperwork.**

Pablo generates SOAP notes from session transcripts with dual-method verification (LLM + classical NLP), ensuring zero hallucinations and full source attribution. Self-host on your own GCP account with one script.

## Features

- **AI SOAP Note Generation** — Upload a session transcript, get a clinically accurate SOAP note
- **Built-in Calendar** — Schedule and manage therapy sessions
- **Patient Management** — Secure patient records with session history
- **HIPAA-Ready** — TLS enforcement, audit logging, encryption at rest

## Quick Start

### Prerequisites

- A Google Cloud Platform account with billing enabled
- `gcloud` CLI installed ([install guide](https://cloud.google.com/sdk/docs/install))

### One-Click Deploy

[![Open in Cloud Shell](https://gstatic.com/cloudssh/images/open-btn.svg)](https://shell.cloud.google.com/cloudshell/editor?cloudshell_git_repo=https://github.com/pablo-health/pablo.git&cloudshell_open_in_editor=setup-solo.sh&cloudshell_tutorial=setup-solo.sh)

Then run:
```bash
./setup-solo.sh
```

The setup script walks you through everything: GCP project creation, Firestore, Firebase Auth, AI model selection, and Cloud Run deployment.

### Manual Deploy

```bash
git clone https://github.com/pablo-health/pablo.git
cd pablo
./setup-solo.sh
```

### Local Development

```bash
# Backend
cd backend && poetry install && poetry run uvicorn app.main:app --reload

# Frontend
cd frontend && pnpm install && pnpm dev

# Or use Docker
docker compose up
```

## Architecture

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 15, TypeScript, Tailwind CSS, shadcn/ui |
| Backend | FastAPI, Python 3.13, PostgreSQL/Firestore |
| AI | Google Gemini or Anthropic Claude |
| Infra | Docker, Google Cloud Run |

## HIPAA Compliance

Self-hosting Pablo means **you** are responsible for HIPAA compliance. See our [Self-Hosting HIPAA Guide](docs/SELF_HOSTING_HIPAA_GUIDE.md) for what you need to do.

## Want Managed Hosting?

**Pablo Solo** ($19-24/mo) handles infrastructure, HIPAA compliance, BAA coverage, backups, and updates — so you can focus on your clients.

Learn more at [pablo.health](https://pablo.health)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

[AGPL-3.0](LICENSE) — You can self-host and modify Pablo freely. If you host a modified version as a service, you must release your changes under the same license.

## Registration

If you deploy Pablo for clinical use, please email support@pablo.health so we can notify you of critical security updates. This is a condition of using Pablo in production.
