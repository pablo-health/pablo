# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
# shellcheck shell=bash
#
# The VM for install.sh: its address, firewall, data disk and the instance.
# Sourced, not run. Every function is safe to repeat. Expects PROJECT_ID,
# REGION, ZONE, MACHINE_TYPE, DATA_DISK_GB and VM_SA.

readonly VM_NAME=pablo
readonly VM_TAG=pablo-web
readonly DATA_DISK=pablo-data
# Google's range for SSH through Identity-Aware Proxy.
readonly IAP_RANGE=35.235.240.0/20

# Sets VM_IP. Reserved before anything else so the hostname is known up front.
ensure_static_ip() {
    if ! gcloud compute addresses describe "$VM_NAME" --region="$REGION" >/dev/null 2>&1; then
        gcloud compute addresses create "$VM_NAME" --region="$REGION" >/dev/null
    fi
    VM_IP=$(gcloud compute addresses describe "$VM_NAME" --region="$REGION" --format='value(address)')
}

_ensure_firewall_rule() {
    local name="$1" ports="$2" source="$3"
    gcloud compute firewall-rules describe "$name" >/dev/null 2>&1 && return
    gcloud compute firewall-rules create "$name" --network=default --direction=INGRESS \
        --allow="$ports" --source-ranges="$source" --target-tags="$VM_TAG" >/dev/null
}

# Web from anywhere (Let's Encrypt has to reach port 80 too); SSH only
# through IAP, so the VM takes no SSH from the internet.
ensure_firewall() {
    _ensure_firewall_rule pablo-allow-web tcp:80,tcp:443,udp:443 0.0.0.0/0
    _ensure_firewall_rule pablo-allow-iap-ssh tcp:22 "$IAP_RANGE"
}

# Its own disk, kept when the VM is deleted: the database outlives the VM.
ensure_data_disk() {
    gcloud compute disks describe "$DATA_DISK" --zone="$ZONE" >/dev/null 2>&1 && return
    gcloud compute disks create "$DATA_DISK" --zone="$ZONE" \
        --size="${DATA_DISK_GB}GB" --type=pd-balanced >/dev/null
    gcloud compute disks add-resource-policies "$DATA_DISK" --zone="$ZONE" \
        --resource-policies="$(_ensure_snapshot_schedule)" >/dev/null
}

# Daily disk snapshots, a second line behind pgBackRest.
_ensure_snapshot_schedule() {
    local policy=pablo-daily-snapshots
    if ! gcloud compute resource-policies describe "$policy" --region="$REGION" >/dev/null 2>&1; then
        gcloud compute resource-policies create snapshot-schedule "$policy" --region="$REGION" \
            --daily-schedule --start-time=08:00 --max-retention-days=14 \
            --on-source-disk-delete=keep-auto-snapshots >/dev/null
    fi
    echo "$policy"
}

# ensure_vm <config file> — creates the VM, or updates its config and
# reboots it so bootstrap.sh converges on the new one.
ensure_vm() {
    local config="$1"
    local metadata="startup-script=${DEPLOY_DIR}/bootstrap.sh,pablo-config=${config}"
    if gcloud compute instances describe "$VM_NAME" --zone="$ZONE" >/dev/null 2>&1; then
        gcloud compute instances add-metadata "$VM_NAME" --zone="$ZONE" --metadata-from-file="$metadata" \
            --metadata=enable-guest-attributes=TRUE >/dev/null
        echo "Restarting the VM to apply the new configuration"
        gcloud compute instances reset "$VM_NAME" --zone="$ZONE" >/dev/null
        return
    fi
    gcloud compute instances create "$VM_NAME" --zone="$ZONE" \
        --machine-type="$MACHINE_TYPE" \
        --image-family=debian-12 --image-project=debian-cloud \
        --boot-disk-size=20GB --boot-disk-type=pd-balanced \
        --disk="name=${DATA_DISK},device-name=${DATA_DISK},mode=rw,auto-delete=no" \
        --address="$VM_IP" --tags="$VM_TAG" \
        --service-account="$VM_SA" --scopes=cloud-platform \
        --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
        --metadata=enable-oslogin=TRUE,block-project-ssh-keys=TRUE,enable-guest-attributes=TRUE \
        --metadata-from-file="$metadata" >/dev/null
}
