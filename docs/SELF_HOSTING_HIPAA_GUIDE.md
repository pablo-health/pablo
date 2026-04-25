# Self-Hosting HIPAA Compliance Guide

Self-hosting Pablo means **you** are the HIPAA covered entity (or business associate) responsible for protecting patient health information (PHI). This guide covers what you need to do.

## 1. Business Associate Agreement (BAA)

You need a BAA with every vendor that handles PHI on your behalf.

| | Self-Hosted | Pablo Solo |
|---|---|---|
| Cloud provider BAA | You sign directly with Google Cloud | We cover it |
| AI model provider BAA | You sign with Google (Gemini) or Anthropic (Claude) | We cover it |
| Transcription provider BAA | Depends on provider — see below | We cover it |
| Pablo BAA | Not needed — you host it yourself | Included |

**How to sign the Google Cloud BAA:**
1. Go to Google Cloud Console > Settings > Compliance
2. Review and accept the BAA
3. This covers Cloud SQL, Cloud Run, Cloud Storage, Cloud Batch, and all GCP services you use

**How to sign the AI provider BAA:**
- **Google Gemini**: Covered by the Google Cloud BAA
- **Anthropic Claude**: Contact Anthropic sales for a BAA if using their API directly

**How to sign the transcription provider BAA:**

Session audio is PHI. Pablo supports two transcription backends, chosen via the `TRANSCRIPTION_PROVIDER` environment variable:

| Provider | Default | BAA requirement |
|---|---|---|
| `whisper` (recommended for self-host) | Yes, in `settings.py` | Already covered — Cloud Batch + Cloud Storage are under the Google Cloud BAA above. No separate vendor BAA needed. |
| `assemblyai` | Opt-in | **You must sign a direct BAA with AssemblyAI before processing any real PHI.** Contact AssemblyAI sales to request one. The default free/trial tier does **not** include a BAA. |

If you deploy with `TRANSCRIPTION_PROVIDER=assemblyai` and no signed BAA, session audio leaving Pablo for AssemblyAI is a §164.504(e) impermissible disclosure and a §164.402 Breach. The `whisper` path keeps every byte inside your GCP project.

**How `setup-solo.sh` handles this:**

During Step 9 (Cloud Run deploy), the script prompts you to choose a transcription provider:

- If you pick **AssemblyAI**, the script requires you to type `I have a signed AssemblyAI BAA` verbatim before continuing, then prompts for the API key (input hidden), stores it in Secret Manager as `pablo-assemblyai-api-key`, and wires the secret into the backend deploy. There is no way to deploy onto the AssemblyAI path without the explicit BAA acknowledgement.
- If you pick **Whisper**, the script currently aborts with instructions, because the whisper path needs Cloud Batch + GPU + a worker container image that `setup-solo.sh` does not yet provision. That provisioning is tracked as a follow-up; once it lands, `whisper` will become the default.

On a re-run, if `pablo-assemblyai-api-key` already exists in Secret Manager, the script reuses it without re-prompting.

## 2. Access Control

HIPAA requires that only authorized individuals can access PHI.

| | Self-Hosted | Pablo Solo |
|---|---|---|
| Authentication | You configure Identity Platform | Pre-configured |
| MFA | You enable TOTP or use IAP | Mandatory TOTP |
| User management | You manage the allowlist | We manage it |

**Your options:**

- **MFA (default)**: Pablo ships with TOTP MFA support. Enable it in Identity Platform during setup.
- **Google Cloud IAP**: If you deploy behind Identity-Aware Proxy, users authenticate via their Google account at the load balancer level before reaching the app. This satisfies HIPAA access control requirements without app-level MFA. The setup script offers this as an option.

## 3. Encryption

| | Self-Hosted | Pablo Solo |
|---|---|---|
| In transit | Cloud Run provides automatic TLS | Same |
| At rest | Cloud SQL encrypts by default (AES-256) | Same |
| OAuth tokens | AES-256 encrypted (you generate the key) | We manage the key |

**What you need to do:**
- Ensure `ENFORCE_HTTPS=true` (this is the default)
- Generate an encryption key for Google Calendar OAuth tokens if using scheduling: `openssl rand -base64 32`
- Store secrets in GCP Secret Manager (the setup script handles this)

## 4. Audit Logging

HIPAA requires audit trails for PHI access.

| | Self-Hosted | Pablo Solo |
|---|---|---|
| Application logs | Cloud Run logs to Cloud Logging | Same |
| Data access logs | You enable Cloud Audit Logs | Pre-configured |
| Log retention | You configure retention policy | We manage it |

**What you need to do:**
1. Enable Data Access audit logs for Cloud SQL in GCP Console
2. Set a log retention period (HIPAA requires minimum 6 years)
3. Restrict access to logs to authorized personnel

## 5. Backup and Disaster Recovery

| | Self-Hosted | Pablo Solo |
|---|---|---|
| Database backups | You enable Cloud SQL automated backups | Automated daily |
| Backup testing | You verify restores work | We verify |
| Recovery plan | You document and test it | We maintain it |

**What you need to do:**
1. Enable Cloud SQL automated backups (daily) and point-in-time recovery
2. Test restoring from backup at least annually
3. Document your recovery procedure

## 6. Network Security

| | Self-Hosted | Pablo Solo |
|---|---|---|
| TLS certificates | Google-managed (Cloud Run) | Same |
| DDoS protection | Cloud Run built-in | Same + Cloud Armor |
| Firewall rules | You configure if needed | We manage |

Cloud Run provides a good security baseline. For additional protection, consider enabling Cloud Armor.

## 7. Staying Up to Date

**You are responsible for pulling updates when we release them.** Security patches, bug fixes, and new features will not apply automatically to your deployment.

| | Self-Hosted | Pablo Solo |
|---|---|---|
| Security patches | You pull and redeploy | Automatic |
| Feature updates | You pull and redeploy | Automatic |
| Dependency updates | You pull and redeploy | Automatic |
| Downtime during updates | You manage | Zero-downtime deploys |

**What you need to do:**
1. Watch the [pablo-health/pablo](https://github.com/pablo-health/pablo) repo for releases
2. When a new release is published, review the changelog
3. Pull the update and run `./redeploy.sh` to apply it
4. Test that everything works after the update

## 8. Register Your Deployment

If you deploy Pablo for clinical use, you **must** register your deployment at [pablo.health/register](https://pablo.health/register). This is how we notify you of:

- Critical security vulnerabilities that need immediate patching
- Breaking changes that affect your deployment
- HIPAA-relevant updates (e.g., changes to encryption or audit logging)

We will never spam you or share your information. This is solely for your protection and your patients' safety.

---

## Summary Checklist

- [ ] Signed BAA with Google Cloud
- [ ] Signed BAA with AI provider (if using Anthropic Claude)
- [ ] Configured authentication (MFA or IAP)
- [ ] Verified HTTPS enforcement is active
- [ ] Generated and stored encryption keys in Secret Manager
- [ ] Enabled Cloud Audit Logs for Cloud SQL
- [ ] Set log retention to 6+ years
- [ ] Enabled Cloud SQL automated backups and point-in-time recovery
- [ ] Tested restore from backup
- [ ] Documented your disaster recovery plan
- [ ] Registered your deployment at pablo.health/register
- [ ] Subscribed to releases on GitHub for update notifications

---

## Let Us Handle It

We built Pablo because we believe therapists shouldn't drown in paperwork. If managing infrastructure isn't your thing, we're here to help.

**Pablo Solo** ($19-24/mo) handles all of the above — BAA coverage, automatic updates, backups, audit logging, and HIPAA compliance — so you can focus on your clients.

Learn more at [pablo.health](https://pablo.health)
