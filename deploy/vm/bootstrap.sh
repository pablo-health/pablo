#!/usr/bin/env bash
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
#
# The VM half of install.sh. Attached as the instance's startup script, so it
# runs as root on every boot; each step checks before it acts, so a reboot or
# a re-run of install.sh converges instead of repeating work.
#
#   1. Config from instance metadata (written by install.sh) → /etc/pablo
#   2. Packages: Docker, Postgres 16 + pgaudit + pgBackRest (PGDG), Ops Agent
#   3. The data disk, mounted at /mnt/disks/pablo (formatted only if blank)
#   4. Postgres on that disk, reachable from the Compose network only
#   5. pgBackRest to the backup bucket, with the VM's own identity
#   6. The pablo-* commands, from the release's deploy files
#   7. pablo-update: deploy files, env from Secret Manager, migrate, start
#   8. The first clinician's email on the allowlist, and a first backup
#
# Google credentials come from the metadata server everywhere — gcloud,
# pgBackRest, the containers, the Ops Agent. No key file exists on this VM.

set -euo pipefail

readonly METADATA="http://metadata.google.internal/computeMetadata/v1/instance/attributes"
readonly DATA_DEVICE="/dev/disk/by-id/google-pablo-data"
readonly DATA_MOUNT="/mnt/disks/pablo"
readonly PG_VERSION=16
readonly PG_DATA="${DATA_MOUNT}/postgresql/${PG_VERSION}/main"
readonly PG_CONF_DIR="/etc/postgresql/${PG_VERSION}/main"

log() { echo "[pablo-bootstrap] $*"; }

# install.sh waits on this, not on /api/health: the site answers before the
# first clinician is admitted and the first backup exists. Tagged with the
# run's id so a previous boot's answer never counts for this one.
report() {
    curl -fsS -X PUT -H "Metadata-Flavor: Google" --data "$1 ${RUN_ID:-}" \
        "http://metadata.google.internal/computeMetadata/v1/instance/guest-attributes/pablo/state" >/dev/null || true
}
STEP="starting"
trap 'report "failed at: ${STEP}"' ERR

# --- 1. Config -------------------------------------------------------------
mkdir -p /etc/pablo /var/lib/pablo/caddy /opt/pablo
curl -fsS -H "Metadata-Flavor: Google" "${METADATA}/pablo-config" > /etc/pablo/config.env
chmod 0644 /etc/pablo/config.env
# shellcheck source=/dev/null
source /etc/pablo/config.env
secret() { gcloud secrets versions access latest --secret="$1" --project="$PROJECT_ID"; }

# --- 2. Packages -----------------------------------------------------------
STEP="installing packages"
report installing
install_packages() {
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -q
    apt-get install -yq ca-certificates curl gnupg git unattended-upgrades

    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /etc/apt/keyrings/pgdg.asc
    local codename
    # shellcheck source=/dev/null
    codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"
    echo "deb [signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${codename} stable" \
        > /etc/apt/sources.list.d/docker.list
    echo "deb [signed-by=/etc/apt/keyrings/pgdg.asc] https://apt.postgresql.org/pub/repos/apt ${codename}-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list

    apt-get update -q
    # postgresql-common first, with its auto-created cluster switched off:
    # the cluster lives on the data disk and is created below.
    apt-get install -yq postgresql-common
    sed -i 's/^#\?create_main_cluster.*/create_main_cluster = false/' /etc/postgresql-common/createcluster.conf
    apt-get install -yq docker-ce docker-ce-cli containerd.io docker-compose-plugin \
        "postgresql-${PG_VERSION}" "postgresql-${PG_VERSION}-pgaudit" pgbackrest

    curl -fsSL https://dl.google.com/cloudagents/add-google-cloud-ops-agent-repo.sh | bash -s -- --also-install
    dpkg-reconfigure -f noninteractive unattended-upgrades
}

if ! command -v docker >/dev/null || ! command -v pgbackrest >/dev/null; then
    log "installing packages"
    install_packages
fi

# Container logs straight to Cloud Logging under this VM. `docker logs` still
# works locally (dual logging).
if [ ! -f /etc/docker/daemon.json ]; then
    cat > /etc/docker/daemon.json <<'JSON'
{ "log-driver": "gcplogs", "log-opts": { "gcp-log-cmd": "true" } }
JSON
    systemctl restart docker
fi

# --- 3. Data disk ----------------------------------------------------------
STEP="preparing the data disk"
if ! blkid "$DATA_DEVICE" >/dev/null 2>&1; then
    log "formatting the new data disk"
    mkfs.ext4 -q -m 0 -E lazy_itable_init=0,lazy_journal_init=0,discard "$DATA_DEVICE"
fi
mkdir -p "$DATA_MOUNT"
if ! grep -q "$DATA_MOUNT" /etc/fstab; then
    echo "UUID=$(blkid -s UUID -o value "$DATA_DEVICE") ${DATA_MOUNT} ext4 discard,defaults,nofail 0 2" >> /etc/fstab
fi
mountpoint -q "$DATA_MOUNT" || mount "$DATA_MOUNT"

# --- 4. Postgres -----------------------------------------------------------
STEP="setting up the database"
# A fresh boot disk with an existing data disk (VM recreated) adopts the data
# directory: pg_createcluster integrates a non-empty one rather than init it.
if [ ! -d "$PG_CONF_DIR" ]; then
    log "registering the Postgres cluster on the data disk"
    mkdir -p "$(dirname "$PG_DATA")"
    chown -R postgres:postgres "${DATA_MOUNT}/postgresql"
    pg_createcluster "$PG_VERSION" main -d "$PG_DATA" -- --data-checksums
fi

# Sized for a 2 GB VM shared with the backend and frontend.
cat > "${PG_CONF_DIR}/conf.d/pablo.conf" <<CONF
listen_addresses = '127.0.0.1,172.30.0.1'
max_connections = 50
shared_buffers = 256MB
effective_cache_size = 768MB
work_mem = 8MB
maintenance_work_mem = 64MB
shared_preload_libraries = 'pgaudit'
# Role and DDL changes only. Relation logging re-logged the whole tenant
# template on every provisioning and pushed it past the idle-in-transaction
# limit; the app's own audit log is the record of data access.
pgaudit.log = 'role, ddl'
pgaudit.log_relation = off
pgaudit.log_statement_once = on
archive_mode = on
archive_command = 'pgbackrest --stanza=pablo archive-push %p'
wal_level = replica
CONF

# The containers' network, created here rather than by Compose so its gateway
# address exists before Postgres binds it.
docker network inspect pablo >/dev/null 2>&1 \
    || docker network create --subnet 172.30.0.0/24 --gateway 172.30.0.1 pablo >/dev/null

HBA_LINE="host all pablo 172.30.0.0/24 scram-sha-256"
grep -qF "$HBA_LINE" "${PG_CONF_DIR}/pg_hba.conf" || echo "$HBA_LINE" >> "${PG_CONF_DIR}/pg_hba.conf"

# Restarted on every boot: Postgres can start before Docker has brought the
# network's gateway up, and would then serve on loopback only.
systemctl enable "postgresql@${PG_VERSION}-main"
systemctl restart "postgresql@${PG_VERSION}-main"

DB_PASSWORD="$(secret pablo-db-password)"
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='pablo'" | grep -q 1; then
    log "creating the pablo role and database"
    # CREATEROLE/CREATEDB, not superuser: migrate creates its own roles.
    sudo -u postgres psql -v ON_ERROR_STOP=1 -v pw="$DB_PASSWORD" <<'SQL'
CREATE ROLE pablo LOGIN CREATEROLE CREATEDB PASSWORD :'pw';
CREATE DATABASE pablo OWNER pablo;
ALTER ROLE pablo SET pgaudit.log = 'none';
SQL
fi
unset DB_PASSWORD

# --- 5. Backups ------------------------------------------------------------
STEP="setting up backups"
cat > /etc/pgbackrest.conf <<CONF
[global]
repo1-type=gcs
repo1-gcs-bucket=${BACKUP_BUCKET}
repo1-gcs-key-type=auto
repo1-path=/pgbackrest
repo1-retention-full=4
start-fast=y
compress-type=zst
log-level-console=info

[pablo]
pg1-path=${PG_DATA}
CONF
chmod 0640 /etc/pgbackrest.conf
chown postgres:postgres /etc/pgbackrest.conf
sudo -u postgres pgbackrest --stanza=pablo stanza-create

# --- 6-7. Deploy files, commands, the stack ---------------------------------
STEP="starting Pablo"
# The commands come fresh from the deploy ref on every boot, so a boot that
# stopped halfway never leaves a broken pablo-update behind. pablo-update
# then fetches the full deploy files and installs that release's commands.
ref="$(cat /etc/pablo/ref 2>/dev/null || echo "${DEPLOY_REF:-$PABLO_VERSION}")"
commands="$(mktemp -d)"
git clone -q --depth 1 --branch "$ref" --filter=blob:none --sparse "$REPO_URL" "$commands"
git -C "$commands" sparse-checkout set deploy/vm/bin
install -m 0755 "$commands"/deploy/vm/bin/pablo-* /usr/local/bin/
rm -rf "$commands"
/usr/local/bin/pablo-update
PABLO_VERSION="$(cat /etc/pablo/version)"

# --- 8. First clinician ----------------------------------------------------
STEP="admitting the first clinician and taking the first backup"
if [ ! -f /var/lib/pablo/admin-allowed ]; then
    # From the deploy files rather than the image, so it works with any
    # release image, including ones built before the script existed.
    allow_email="$(readlink -f /opt/pablo/current)/../../backend/bin/allow_email.py"
    PABLO_VERSION="$PABLO_VERSION" PABLO_HOST="$PABLO_HOST" \
        docker compose -f /opt/pablo/current/compose.yaml run --rm \
        -v "$(readlink -f "$allow_email"):/app/backend/bin/allow_email.py:ro" \
        tool backend/bin/allow_email.py "$ADMIN_EMAIL"
    touch /var/lib/pablo/admin-allowed
fi

# A first full backup now, so there is one to restore before the weekly run.
if ! sudo -u postgres pgbackrest --stanza=pablo info --output=json | grep -q '"label"'; then
    log "taking the first full backup"
    /usr/local/bin/pablo-backup full
fi

log "ready: https://${PABLO_HOST}"
report ready
