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
| Backups | pgBackRest to `gs://<project>-pablo-backups`, encrypted with a passphrase the installer shows you once: weekly full, daily differential, WAL continuously; daily disk snapshots too |
| Restore check | monthly: the latest backup is restored into a scratch copy and queried |
| Short-lived state | Redis, in memory only: calendar-connect handshakes, passkey challenges, rate limits, the idle-session clock |
| Scheduled jobs | `/etc/cron.d/pablo`: HIPAA log reviews, pipeline heartbeat, quarterly attestation |
| Logs | containers and jobs to Cloud Logging; audit events kept six years by the audit sink, in a bucket whose retention is locked |
| Sign-in | Identity Platform: email and Google, 15-character passwords, authenticator-app MFA |

Google credentials come from the VM's own service account (`pablo-vm`)
through the metadata server. There is no key file on the VM. SSH is allowed
only through Identity-Aware Proxy. Only ports 80 and 443 are open to the
internet.

Caddy is the only way in. The backend's env file sets `TRUSTED_PROXY_IPS` to
Caddy's fixed address on the Docker network, so the backend takes the client
address from Caddy's `X-Forwarded-For` and counts sign-in and booking limits
per client. If you put the backend behind a different proxy, list that proxy's
address instead, or every client shares the proxy's one budget.

## Address

The installer asks how you and your clients reach Pablo, or takes a flag:

| Choice | Flag | What you do |
|---|---|---|
| Your own domain | `--hostname clinic.example.com` | Add one A record pointing it at the address the script prints. The certificate is issued once the record resolves. |
| A name at the hosted-subdomain service | `--hosted-name yourpractice` | Nothing: the VM claims `yourpractice.pablo.clinic` when it starts. |
| An automatic address | neither | Nothing: you get `<ip-with-dashes>.sslip.io`, which works but looks odd to clients. |

Running `install.sh` again keeps the address the VM has, unless you pass
`--hostname` or `--hosted-name`.

### The hosted-subdomain service

An optional service, configurable per deployment, that keeps DNS records for
names under one domain. The default is `https://names.pablo.clinic`, with
names under `pablo.clinic`; `--names-endpoint` and `--names-zone` point the
installer at another. Names are 3 to 40 lowercase letters, numbers and
hyphens, and cannot start or end with a hyphen. The installer checks the name
is free before it creates anything.

It does DNS and nothing more. It holds one A record for your name, set to the
address the VM's request comes from, which is the VM's static IP. It never
sees your traffic: browsers connect straight to the VM, HTTPS ends at Caddy on
the VM, and the certificate is the VM's own, from Let's Encrypt. The VM proves
who it is with the identity token Google gives it, so there is no key to keep.
The name belongs to your Google Cloud project, so a new VM in the same project
can claim it again.

The VM claims the name on every boot, waits until the zone's nameservers
answer with its address, and only then starts Caddy, so Let's Encrypt never
sees the name missing. It renews the name every week (`/etc/cron.d/pablo`). A
name not renewed for 30 days loses its record. Renewals log a JSON line to
Cloud Logging under `pablo-name`, for an alert on `"status":"failed"`.

```bash
sudo pablo-name status           # what the service and DNS say about the name
sudo pablo-name claim            # claim or renew it now
sudo pablo-name release OLDNAME  # give up a name this VM no longer uses
```

To move to your own domain later, add its A record, run `install.sh` again
with `--hostname clinic.example.com`, then release the old name. The installer
reminds you how; it never releases one on its own. A VM refuses to release the
name it still uses, since the next boot would only claim it back.

A name belongs to the Google Cloud project, not the VM. After deleting the VM
and installing again in the same project, the installer's check sees the name
as taken, because it cannot tell who is asking. Add `--reuse-hosted-name` (or
answer yes when asked whether it is yours): the new VM's claim is a renewal for
this project, and is refused for any other.

## Day to day

```bash
gcloud compute ssh pablo --zone=us-central1-a --tunnel-through-iap

sudo pablo-update v1.2.0         # move to a release: pull, migrate, restart
sudo pablo-backup info           # what the backup bucket holds
sudo pablo-backup restore-drill  # restore the latest backup into a scratch copy
sudo pablo-job hipaa_log_review  # run a scheduled job now
sudo pablo-config list           # the VM's settings, and where each comes from
sudo pablo-name status           # the hosted name, if the VM has one
```

An update restarts the app in place, so expect about a minute of downtime.
Two copies side by side do not fit in 2 GB next to Postgres.

The VM's startup script is `boot-stub.sh`. It fetches the deploy files for
the release the VM runs and hands over to that release's `bootstrap.sh`, so
a change to the boot steps arrives with `pablo-update` like everything else.

Settings live in `/etc/pablo/config.env`, which `install.sh` writes and every
boot rewrites, so an edit there does not survive a reboot. A setting the VM
keeps for itself goes in `/etc/pablo/local.env`, through `pablo-config set
KEY=VALUE`; it is read after `config.env` and wins. `pablo-config` takes only
the settings named in its own list, and nothing restarts when one changes:
`sudo pablo-update` applies it.

## Updates from the app

Once a week, and after every update, the VM asks GitHub for the latest
published release of the repository it was installed from (`pablo-update
--check`). When that release is newer than the one running, the practice's
owner and admins see a notice in the app with the version, a link to its
release notes and an **Update now** button. A repository not on GitHub gets
no notice; `pablo-update vX.Y.Z` still works.

The button can only ask for an update; it cannot choose one. The request the
app writes says who asked and when, and nothing else is read from it. The VM
then:

1. asks GitHub again for the latest published release, and stops unless it is
   a `vX.Y.Z` tag newer than the one running (drafts and pre-releases are
   never "latest")
2. takes a differential backup, and stops if that fails
3. runs `pablo-update` to the new release
4. checks the new release is the one serving and the app answers healthy
5. if not, runs `pablo-update` back to the previous release

The app shows "Updating" while this runs and reports what happened. To see
it on the VM:

```bash
journalctl -u pablo-update-request          # the last update asked for in the app
cat /var/lib/pablo/update/host/status.json  # its result
```

Going back does not undo migrations; every update already runs them against
the old release before restarting, so the old release works on the new
schema. Only one update runs at a time: `pablo-update` refuses to start
while another is running, whichever way it was started.

How the pieces meet, all under `/var/lib/pablo/update`:

| Path | Written by | Read by |
|---|---|---|
| `host/latest.json` | `pablo-update --check` | the backend (read-only mount) |
| `host/status.json` | `pablo-update-request` | the backend, and the browser at `/update-status.json` through Caddy |
| `requests/update.json` | the backend, its only writable host path | `pablo-update-request.path`, which starts the update |

Running `install.sh` again is safe. It picks up where it stopped, updates the
VM's settings and restarts it, on the release it already runs; `pablo-update`
is what moves releases. It never touches the data disk or `local.env`.

## Cost

About $20 a month: the e2-small is about $12, the two 20 GB disks about $4,
the static address about $3.65, plus a little for storage and snapshots. Vertex AI and AssemblyAI are billed by use. The measured
figures for memory, CPU and first boot belong here once they are taken
(`PABLO-0z5a.2`).

## Files

- `../../install.sh`: the Cloud Shell half, with `lib/` (project, sign-in,
  secrets, VM, the hosted name check)
- `boot-stub.sh`: the VM's startup script, which runs the release's
  `bootstrap.sh` on every boot
- `compose.yaml`, `Caddyfile`: the stack; `routes.caddy`: how the address splits
  between frontend and backend, shared with `deploy/workstation`
- `lib/env.sh`: the backend and frontend env files, shared with the local target
- `bin/`: `pablo-update`, `pablo-update-request`, `pablo-backup`, `pablo-job`, `pablo-config`,
  `pablo-name`
- `systemd/`: the path unit and service behind the app's **Update now**
- `crontab`: backups, scheduled jobs, the weekly release check and the hosted
  name's renewal
