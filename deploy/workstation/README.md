# Pablo on your own machine

The whole engine on a Mac, or on Windows with WSL or Linux, with a public
HTTPS address from Tailscale Funnel:

```bash
./install.sh --target local
```

It sets up the same Google Cloud project as the VM install (sign-in, AI,
storage, task queues), then runs Pablo in Docker on this machine at
`https://<machine>.<tailnet>.ts.net:8443`, with nightly backups to your
Google Cloud account.

## Trying it out, or running your practice

For trying Pablo or developing on it, nothing more is needed.

You can also run your practice on it. If you do, this machine holds your
clients' records, and keeping them safe is on you:

- **Encrypt the disk** (FileVault on a Mac, BitLocker on Windows, LUKS on
  Linux) and lock the screen when you step away. The installer warns if
  FileVault is off.
- **Use an operating system that still gets security updates.** An older
  machine is fine on a current Linux; an old macOS that no longer gets
  updates is not.
- **Keep backups off the machine, and check them.** Pablo dumps the database
  to your Google Cloud backup bucket every night and keeps 30 days. A restore
  can lose up to a day of changes. Run `pablo-local restore-drill` now and
  then.
- **Keep it awake and online while you work.** Notes and transcription finish
  only then.
- **Keep Docker, Tailscale and the OS updated.**
- **Ask about Tailscale.** Funnel relays the encrypted connection to your
  machine, where it is decrypted. Check with whoever advises you on HIPAA
  whether that needs anything further.

For most practices the [single VM](../vm/README.md), about $20 a month, is
simpler: Google looks after the disk, the backups and the uptime.

## Before you start

1. **Docker Desktop** (Docker Engine on Linux), with at least 4 GB of memory for Docker. On Apple
   Silicon, turn on *Use Rosetta* in Docker Desktop's settings.
2. **gcloud**, signed in twice: `gcloud auth login` (to set up the project)
   and `gcloud auth application-default login` (the containers use this one).
3. **Tailscale**, signed in, with Funnel allowed for this machine. Run this
   once and approve the link it prints:
   ```bash
   tailscale funnel --bg --https=8443 http://127.0.0.1:8080
   ```

## Hardware

| | Minimum | Recommended |
|---|---|---|
| Memory | 8 GB, 4 GB of it for Docker | 16 GB (on Windows, WSL2 and Docker Desktop each take a share) |
| CPU | 2 cores | 4 or more |
| Disk | 10 GB free | 20 GB |
| OS | macOS 13+, Windows 10/11 with WSL2, or Linux (e.g. Ubuntu 24.04) | |

Idle, the containers use about 650 MB together (backend about 415 MB,
frontend 160, Postgres 45, Redis and Caddy about 25). Generating a note or
importing records uses more. The images are amd64, so Apple Silicon runs them
under emulation.

## Why a public address

Note generation, transcription and calendar sync run as Cloud Tasks that call
back into the backend, so the machine needs an address Google can reach.
Funnel provides it and its certificate. Caddy, on loopback port 8080, splits
the routes between frontend and backend exactly as on the VM
(`deploy/vm/routes.caddy`).

## Credentials

There is no key file. The containers use your own application-default
sign-in, impersonating the project's `pablo-vm` service account; the installer
grants you permission to do that and writes the credentials to
`~/.pablo/<project>/`. If your Google Workspace makes you sign in again
periodically, run `gcloud auth application-default login` and then
`./install.sh --target local --project <project>` to refresh them.

## Backups

```bash
~/.pablo/<project>/bin/pablo-local backup         # back up now
~/.pablo/<project>/bin/pablo-local list           # what the bucket holds
~/.pablo/<project>/bin/pablo-local restore-drill  # restore the latest into a scratch database
```

The nightly run is a launchd job on a Mac (it runs on wake if the Mac was
asleep at 2:30) and a cron entry on WSL or Linux; its log is
`~/.pablo/<project>/backup.log`.

## Day to day

Notes and transcription finish only while this machine is awake and online.

```bash
# stop (data is kept)
PABLO_LOCAL_DIR=~/.pablo/<project> PABLO_VERSION=<version> \
    docker compose -f deploy/workstation/compose.yaml down

# start again, or update to a newer release
./install.sh --target local --project <project> [--version vX.Y.Z]

# close the public address
tailscale funnel --https=8443 off
```
