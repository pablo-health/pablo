# Operating the VM install

Day-to-day care of a self-hosted Pablo: backups, restores, the scheduled
jobs, the audit sink, and what to watch. Companion to the
[Self-hosting HIPAA guide](../SELF_HOSTING_HIPAA_GUIDE.md).

Every command below runs on the VM:

```bash
gcloud compute ssh pablo --zone=<zone> --tunnel-through-iap
```

Times in `/etc/cron.d/pablo` are UTC.

## Backups

Source: `deploy/vm/bootstrap.sh` (pgBackRest config), `deploy/vm/crontab`,
`deploy/vm/bin/pablo-backup`, `deploy/vm/lib/vm.sh` (snapshots).

| What | When | Kept |
|---|---|---|
| Full backup (pgBackRest) | Sundays 03:00 | Four full backups, and the differentials and WAL that belong to them |
| Differential backup | Monday to Saturday 03:00 | With its full backup |
| WAL archive | Continuously, as Postgres writes it | With its full backup; a restore can reach any moment since the oldest kept full backup |
| First full backup | Taken by the installer before it reports ready | — |
| Disk snapshot of `pablo-data` | Daily 08:00 | 14 days; kept if the disk is deleted |

Backups go to `gs://<project>-pablo-backups/pgbackrest`, compressed with
zstd, written with the VM's own identity. The VM's service account can
write and delete objects in that bucket, so a compromised VM could remove
backups; the disk snapshots are the line behind it, and they live outside
the VM's reach.

```bash
sudo pablo-backup info    # every backup the bucket holds, with sizes and times
sudo pablo-backup full    # take a full backup now
sudo pablo-backup diff    # take a differential now
```

Each run writes `{"event":"job_completed", ...}` or, for the drill,
`{"event":"restore_drill","status":"ok|failed"}` to the system log with the
tag `pablo-backup`.

## The monthly restore drill

On the 2nd of each month at 04:00, `pablo-backup restore-drill`:

1. restores the latest backup into a scratch directory on the data disk;
2. starts a second Postgres on port 5499, local socket only, with WAL
   archiving off so it cannot write into the live backup;
3. counts the rows in `platform.users`;
4. stops and deletes the scratch copy.

It prints "Restore drill passed" and logs `restore_drill` with `status: ok`
and the user count. A failure logs `status: failed` and the last lines of the
scratch server's log. Run it yourself after the install and after any change
to the disk or the backup bucket:

```bash
sudo pablo-backup restore-drill
```

Keep a note of each month's result. It is the evidence that your backups
restore.

## Restoring

There is no `pablo-backup restore` command. The drill proves the backup;
putting it back in place is a pgBackRest operation you do by hand. Practise
it on a second VM before you need it.

**The VM is gone but the data disk is not.** Run `install.sh` again with the
same project. It creates a new VM attached to the existing `pablo-data`
disk, and `bootstrap.sh` adopts the Postgres cluster it finds there. Nothing
is restored from the bucket; the data was never lost.

**The data disk is damaged.** Two options, in order of preference:

1. Create a new disk from the most recent snapshot in the console, name it
   `pablo-data` in the same zone, and run `install.sh` again. You lose
   changes since that snapshot, up to a day.
2. Restore from pgBackRest onto an empty cluster, which can reach any
   moment up to the last archived WAL segment. On the VM, as `postgres`,
   with Postgres stopped, follow pgBackRest's restore documentation for the
   `pablo` stanza (`--delta` restore, and `--type=time --target=...` for a
   point in time). This path is not scripted or tested by the repository.

**A mistake to undo** (a bulk delete, a bad import): a point-in-time restore
from pgBackRest, option 2 above, to a moment before it.

Write the option you will use into your contingency plan, with the zone,
project and bucket names filled in.

## Scheduled jobs

Source: `deploy/vm/crontab`, `deploy/vm/bin/pablo-job`, `backend/app/jobs/`.

Each runs `python -m app.jobs.<name>` in the backend image with the
backend's settings, one at a time, with a one-hour limit. The result is one
JSON line in the system log, tagged `pablo-job`:
`{"event":"job_completed","job":"hipaa_log_review","status":"ok","exit_code":0,"seconds":41}`.

| Job | When | What it does | Where the output goes |
|---|---|---|---|
| `hipaa_log_review` (24 hours) | Daily 07:00 | Deterministic anomaly flags over the last day of `audit_logs` for every practice, then Gemini narrates anything flagged. The input is PHI-free by contract: ids, times, actions, addresses | A markdown report and a JSON run record in `gs://<project>-pablo-compliance/`. A HIGH finding also logs an `ERROR` line with `alert_type=hipaa_review_high` |
| `hipaa_log_review` (720 hours) | 1st of the month 06:00 | The same over 30 days, with a broader look at active practices | Same bucket |
| `pipeline_heartbeat` | Mondays 09:00 | Logs an `ERROR` line with `alert_type=pipeline_heartbeat` and writes `heartbeat/<time>.txt` to the compliance bucket. Proves the job, the bucket and the log path work | Compliance bucket, system log |
| `hipaa_attestation` | 1st of January, April, July, October, 05:00 | A deterministic evidence snapshot: audit row counts and span, retention settings, bucket retention and whether it is locked, compliance bucket inventory. No model involved | `gs://<project>-pablo-compliance/attestations/<date>.md` |

Run any of them now with `sudo pablo-job <name>`, for example
`sudo pablo-job hipaa_log_review REVIEW_WINDOW_HOURS=24`.

**What to do with them.**

- Read the monthly review when it lands. A HIGH finding is a request to
  look: open the practice's audit log for the user and window it names.
  Record what you concluded; that record is your § 164.308(a)(1)(ii)(D)
  activity review.
- Keep the quarterly attestation with your compliance file. It is written
  so an auditor can check each line against the project.
- The heartbeat is for an alert to catch. Until you have one, it only
  confirms in the bucket that the job ran.

The compliance bucket has a 7-year retention policy. It is not locked, so a
mistaken upload can still be removed by a project owner.

Not scheduled: `audit_retention_cron` (would delete audit rows past 7
years) and `hard_purge_cron` (would remove soft-deleted clients; in this
build it does nothing without a hosted component). See
[Data retention and deletion](../SELF_HOSTING_HIPAA_GUIDE.md#data-retention-and-deletion).

## The audit sink

Source: `scripts/setup-hipaa-audit-sink.sh`, called by `install.sh`;
`backend/app/services/audit_cloud_logging.py`.

| Piece | Value |
|---|---|
| Bucket | `gs://<project>-hipaa-audit-6y`, Archive class, location `us` |
| Retention | 2255 days (6 years plus a 60-day margin), **locked** by the installer once it has verified the period on the bucket |
| Sink 1 | Google's admin activity log: IAM changes, VM changes, SSH through IAP, secret access. Google keeps these only 400 days on its own |
| Sink 2 | Pablo's `pablo.audit_events`: a copy of every `audit_logs` row, written by the backend with the Cloud Logging client as each row is saved |

The lock cannot be undone for the retention period, and the bucket cannot
be deleted while it holds objects under retention; the installer's summary
says so before it creates anything. A run with `--no-lock-audit-retention`
leaves the policy unlocked, for an evaluation project you will throw away.
To lock such a project later:

```bash
scripts/setup-hipaa-audit-sink.sh <project-id>
```

Check: `gsutil retention get gs://<project>-hipaa-audit-6y` and
`gcloud logging sinks list --project=<project>`.

Container logs (requests, errors, the jobs' own output) go to Cloud Logging
through Docker's `gcplogs` driver and follow Cloud Logging's default
retention, which is shorter. Only the two sinks above reach the six-year
bucket.

## What to watch

The installer configures no alerts. `scripts/monitoring/setup.sh` exists in
the repository but is written for Cloud Run and Cloud SQL resource types; it
is not run by `install.sh` and its policies would not match this VM. Until
you build your own, check these by hand, on a schedule you write down.

| Watch | How | Why |
|---|---|---|
| Backups are being taken | `sudo pablo-backup info`: the newest backup should be from last night | A silent backup failure is found only at restore time |
| The restore drill passed | System log on the VM: `journalctl -t pablo-backup`, or Cloud Logging for `restore_drill` | Monthly evidence that restores work |
| Jobs completed | `journalctl -t pablo-job` or Cloud Logging for `job_completed` with `status: failed` | A failed review is a gap in your activity review |
| Heartbeat | `heartbeat/` in the compliance bucket gains a file each Monday | Confirms the whole job path |
| HIGH findings | Cloud Logging for `alert_type=hipaa_review_high` | Investigate each one |
| Disk space | `df -h /mnt/disks/pablo` and `/` | Postgres stops writing when the disk fills. The drill needs room for a second copy of the database |
| The site is up | Open it; or an uptime check in Cloud Monitoring on `https://<host>/api/health` | — |
| Certificate | Caddy renews on its own; a browser warning means it could not reach Let's Encrypt on port 80 | — |
| Releases | GitHub releases, and the registration email | Security fixes ship as releases |

If you want email, Cloud Monitoring can do it: a log-based alert on
`jsonPayload.event="job_completed" AND jsonPayload.status="failed"`, another
on `alert_type="hipaa_review_high"`, an uptime check on the site, and a
disk-utilisation condition from the Ops Agent's metrics. The Ops Agent is
installed by `bootstrap.sh`; its default configuration ships the system
log, which is where `pablo-job` and `pablo-backup` write. Whether a given
line arrives as `jsonPayload` or `textPayload` depends on Cloud Logging's
parsing; check with `gcloud logging read` before you rely on a filter.

## Updating

```bash
sudo pablo-update vX.Y.Z
```

Pulls the release's images and deploy files, renders the env files from
Secret Manager, runs the migrations against the old code still serving, then
restarts the stack. About a minute of downtime. The crontab is reinstalled
from the release, so a release can change the schedule above.
