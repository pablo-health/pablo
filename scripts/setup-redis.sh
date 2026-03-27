#!/usr/bin/env bash
# Setup Redis on GCE e2-micro instances for Pablo backend.
#
# Creates one Redis instance per environment (dev, prod) with:
# - Password stored in Secret Manager
# - Firewall rule for VPC-internal access on port 6379
# - Direct VPC egress configured on Cloud Run backend
# - Redis env vars set on Cloud Run (USE_REDIS=false by default)
#
# Usage:
#   ./scripts/setup-redis.sh          # both dev and prod
#   ./scripts/setup-redis.sh dev      # dev only
#   ./scripts/setup-redis.sh prod     # prod only
#
# Prerequisites:
#   - gcloud CLI authenticated with sufficient permissions
#   - Compute Engine API enabled on target projects

set -euo pipefail

DEV_PROJECT="pablohealth-dev"
PROD_PROJECT="pablohealth-prod"
ZONE="us-central1-a"
REGION="us-central1"
INSTANCE_NAME="pablo-redis"

# Which environments to set up
TARGET="${1:-both}"

setup_redis() {
  local project="$1"
  local env_name="$2"
  local redis_password="$3"

  echo "=== Setting up Redis for ${env_name} (${project}) ==="

  # 1. Enable required APIs
  echo "Enabling Compute Engine and VPC Access APIs..."
  gcloud services enable compute.googleapis.com --project="$project" --quiet
  gcloud services enable vpcaccess.googleapis.com --project="$project" --quiet

  # 2. Create GCE instance with Redis startup script
  if gcloud compute instances describe "$INSTANCE_NAME" --project="$project" --zone="$ZONE" &>/dev/null; then
    echo "Instance ${INSTANCE_NAME} already exists in ${project}, skipping creation."
  else
    echo "Creating e2-micro instance with Redis..."
    gcloud compute instances create "$INSTANCE_NAME" \
      --project="$project" \
      --zone="$ZONE" \
      --machine-type=e2-micro \
      --image-family=debian-12 \
      --image-project=debian-cloud \
      --boot-disk-size=10GB \
      --boot-disk-type=pd-standard \
      --tags=redis-server \
      --metadata=startup-script="$(cat <<STARTUP
#!/bin/bash
set -e
if ! command -v redis-server &> /dev/null; then
  apt-get update -qq
  apt-get install -y -qq redis-server
fi
cat > /etc/redis/redis.conf << CONF
bind 0.0.0.0
port 6379
protected-mode yes
requirepass ${redis_password}
maxmemory 256mb
maxmemory-policy allkeys-lru
save 60 1000
save 300 100
dir /var/lib/redis
dbfilename dump.rdb
appendonly yes
appendfilename "appendonly.aof"
CONF
chown redis:redis /var/lib/redis
systemctl enable redis-server
systemctl restart redis-server
STARTUP
)" \
      --quiet
  fi

  # 3. Get instance internal IP
  local redis_ip
  redis_ip=$(gcloud compute instances describe "$INSTANCE_NAME" \
    --project="$project" --zone="$ZONE" \
    --format='value(networkInterfaces[0].networkIP)')
  echo "Redis internal IP: ${redis_ip}"

  # 4. Create firewall rule
  if gcloud compute firewall-rules describe allow-redis --project="$project" &>/dev/null; then
    echo "Firewall rule allow-redis already exists, skipping."
  else
    echo "Creating firewall rule for Redis..."
    gcloud compute firewall-rules create allow-redis \
      --project="$project" \
      --direction=INGRESS \
      --action=ALLOW \
      --rules=tcp:6379 \
      --target-tags=redis-server \
      --source-ranges=10.0.0.0/8 \
      --description="Allow Redis from VPC (Cloud Run Direct VPC egress)" \
      --quiet
  fi

  # 5. Store password in Secret Manager
  if gcloud secrets describe REDIS_PASSWORD --project="$project" &>/dev/null; then
    echo "Secret REDIS_PASSWORD already exists, skipping."
  else
    echo "Storing Redis password in Secret Manager..."
    echo -n "$redis_password" | gcloud secrets create REDIS_PASSWORD \
      --data-file=- --project="$project" --quiet
  fi

  # 6. Grant Cloud Run service account access to secret
  local sa_email
  sa_email="$(gcloud projects describe "$project" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
  echo "Granting secret access to ${sa_email}..."
  gcloud secrets add-iam-policy-binding REDIS_PASSWORD \
    --project="$project" \
    --member="serviceAccount:${sa_email}" \
    --role="roles/secretmanager.secretAccessor" \
    --quiet 2>/dev/null

  # 7. Configure Cloud Run with VPC egress + Redis env vars
  echo "Updating Cloud Run backend with VPC egress and Redis config..."
  gcloud run services update pablo-backend \
    --project="$project" \
    --region="$REGION" \
    --network=default \
    --subnet=default \
    --vpc-egress=private-ranges-only \
    --update-env-vars="REDIS_HOST=${redis_ip},REDIS_PORT=6379,REDIS_DB=0,USE_REDIS=false" \
    --update-secrets="REDIS_PASSWORD=REDIS_PASSWORD:latest" \
    --quiet || echo "WARNING: Cloud Run update failed. You may need to redeploy the backend image."

  echo "=== ${env_name} Redis setup complete ==="
  echo "  Instance: ${INSTANCE_NAME} (${redis_ip}:6379)"
  echo "  USE_REDIS is set to 'false' — flip to 'true' when ready for multi-instance"
  echo ""
}

verify_redis() {
  local project="$1"
  local env_name="$2"
  local redis_password="$3"

  echo "Verifying Redis on ${env_name}..."
  local result
  result=$(gcloud compute ssh "$INSTANCE_NAME" \
    --project="$project" --zone="$ZONE" \
    --command="redis-cli -a '${redis_password}' ping" 2>/dev/null)

  if [ "$result" = "PONG" ]; then
    echo "  ${env_name}: PONG (Redis is healthy)"
  else
    echo "  ${env_name}: FAILED (got: ${result})"
  fi
}

# Passwords — change these for real deployments and update Secret Manager
DEV_PASSWORD="pablo-redis-dev-2026"
PROD_PASSWORD="pablo-redis-prod-2026"

case "$TARGET" in
  dev)
    setup_redis "$DEV_PROJECT" "dev" "$DEV_PASSWORD"
    verify_redis "$DEV_PROJECT" "dev" "$DEV_PASSWORD"
    ;;
  prod)
    setup_redis "$PROD_PROJECT" "prod" "$PROD_PASSWORD"
    verify_redis "$PROD_PROJECT" "prod" "$PROD_PASSWORD"
    ;;
  both)
    setup_redis "$DEV_PROJECT" "dev" "$DEV_PASSWORD"
    setup_redis "$PROD_PROJECT" "prod" "$PROD_PASSWORD"
    verify_redis "$DEV_PROJECT" "dev" "$DEV_PASSWORD"
    verify_redis "$PROD_PROJECT" "prod" "$PROD_PASSWORD"
    ;;
  *)
    echo "Usage: $0 [dev|prod|both]"
    exit 1
    ;;
esac

echo ""
echo "Next steps:"
echo "  1. Implement Redis-backed AuthCodeStore, RateLimiter, TenantCache"
echo "  2. Deploy the backend with the new code"
echo "  3. Set USE_REDIS=true on Cloud Run when ready for multi-instance scaling"
