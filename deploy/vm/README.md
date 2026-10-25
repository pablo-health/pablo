# Pablo on one VM

The whole engine on a single Compute Engine VM in your own Google Cloud
project, for a solo practice. One command in Cloud Shell:

```bash
git clone https://github.com/pablo-health/pablo.git && cd pablo
./install.sh
```

Before running it, accept the Google Cloud BAA for the account that owns the
project. If you transcribe with AssemblyAI, you need a signed BAA with
AssemblyAI too; the script asks.

The script asks for two things it cannot create on its own: an OAuth client
for Google sign-in and Google Calendar (it prints the console steps and the
exact URLs), and the AssemblyAI API key. Both are optional; press Enter to
skip Google, and pass `--no-transcription` to leave transcription off.

## Google sign-in and calendar

One OAuth client, in your own project, covers both. Your calendar data goes
from Google to your VM and nowhere else. Because it is your own app rather
than one Google has reviewed, connecting your calendar shows Google's
"unverified app" screen once. Choose Internal on the consent screen if you
use Google Workspace and that screen does not appear. With External, publish
the app ("In production"): a Testing app's calendar connection expires every
seven days.

## What you get

| Piece | Where |
|---|---|
| Backend, frontend | containers from `ghcr.io/pablo-health/*:<release>` |
| HTTPS | Caddy, with a Let's Encrypt certificate it renews itself |
| Database | Postgres 16 on its own disk (`pablo-data`), kept if the VM is deleted |
| Backups | pgBackRest to `gs://<project>-pablo-backups`: weekly full, daily differential, WAL continuously; daily disk snapshots too |
| Restore check | monthly: the latest backup is restored into a scratch copy and queried |
| Short-lived state | Redis, in memory only: calendar-connect handshakes, passkey challenges, rate limits, the idle-session clock |
| Scheduled jobs | `/etc/cron.d/pablo`: HIPAA log reviews, pipeline heartbeat, quarterly attestation |
| Logs | containers and jobs to Cloud Logging; audit events kept six years by the audit sink |
| Sign-in | Identity Platform: email and Google, 15-character passwords, authenticator-app MFA |

Google credentials come from the VM's own service account (`pablo-vm`)
through the metadata server. There is no key file on the VM. SSH is allowed
only through Identity-Aware Proxy. Only ports 80 and 443 are open to the
internet.

## Hostname

Without `--hostname` you get `<ip-with-dashes>.sslip.io`, which works but
looks odd to clients. With your own domain, add one A record pointing it at
the address the script prints; the certificate is issued once the record
resolves.

## Day to day

```bash
gcloud compute ssh pablo --zone=us-central1-a --tunnel-through-iap

sudo pablo-update v1.2.0         # move to a release: pull, migrate, restart
sudo pablo-backup info           # what the backup bucket holds
sudo pablo-backup restore-drill  # restore the latest backup into a scratch copy
sudo pablo-job hipaa_log_review  # run a scheduled job now
```

An update restarts the app in place, so expect about a minute of downtime.
Two copies side by side do not fit in 2 GB next to Postgres.

Running `install.sh` again is safe. It picks up where it stopped, updates the
VM's settings and restarts it. It never touches the data disk.

## Cost

About $20 a month: the e2-small is about $12, the two 20 GB disks about $4,
the static address about $3.65, plus a little for storage and snapshots. Vertex AI and AssemblyAI are billed by use. The measured
figures for memory, CPU and first boot belong here once they are taken
(`PABLO-0z5a.2`).

## Files

- `../../install.sh`: the Cloud Shell half, with `lib/` (project, sign-in,
  secrets, VM)
- `bootstrap.sh`: the VM's startup script, run on every boot
- `compose.yaml`, `Caddyfile`: the stack
- `bin/`: `pablo-update`, `pablo-backup`, `pablo-job`
- `crontab`: backups and scheduled jobs
