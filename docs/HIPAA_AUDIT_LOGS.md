# HIPAA Audit Logs - Cloud Audit Logs for Firestore

This document covers the audit logging requirements for HIPAA compliance and how they are implemented for Firestore operations in the Pablo.

## Table of Contents

1. [HIPAA Requirements](#hipaa-requirements)
2. [What Gets Logged](#what-gets-logged)
3. [Enabling Audit Logs](#enabling-audit-logs)
4. [Log Retention Strategy](#log-retention-strategy)
5. [Querying and Monitoring Logs](#querying-and-monitoring-logs)
6. [Cost Optimization](#cost-optimization)
7. [Incident Investigation](#incident-investigation)
8. [Enabling DATA_READ Logs](#enabling-data_read-logs)

---

## HIPAA Requirements

**HIPAA Security Rule § 164.312(b) - Audit Controls**: Implement hardware, software, and/or procedural mechanisms that record and examine activity in information systems that contain or use electronic protected health information (ePHI).

**Key Requirements:**
- **Track access to ePHI**: Record who accessed what data and when
- **Monitor administrative activities**: Track configuration changes, user management
- **Retain audit logs**: Maintain logs for 6 years (HIPAA retention requirement)
- **Protect log integrity**: Prevent unauthorized modification or deletion of audit logs
- **Regular review**: Periodically review logs for security incidents and anomalies

**Implementation in this Platform:**
- **Application-level logging**: API requests logged with user_id and timestamp (see `middleware.py`)
- **Database-level logging**: Firestore operations tracked via GCP Cloud Audit Logs
- **Two-layer audit trail**: Provides defense-in-depth for PHI access monitoring

---

## What Gets Logged

### Audit Log Types

GCP Cloud Audit Logs provides three types of logs:

| Log Type | Enabled | Operations Tracked | Cost |
|----------|---------|-------------------|------|
| **Admin Activity** | ✅ Always on (free) | Database creation, index management, backups, import/export | Free |
| **ADMIN_READ** | ✅ Enabled | Administrative reads (collection listing, metadata access) | ~$0.50/GB |
| **DATA_WRITE** | ✅ Enabled | Document creates, updates, deletes (PHI modifications) | ~$0.50/GB |
| **DATA_READ** | ❌ Disabled (cost) | Document reads, queries (view PHI) | ~$0.50/GB |

### Current Configuration

The platform is configured with:
- **ADMIN_READ**: Track administrative access to database metadata
- **DATA_WRITE**: Track all PHI modifications (creates, updates, deletes)

**DATA_READ is intentionally disabled** to minimize costs ($1-5/month vs $20-100/month). It can be enabled later if needed for specific security incidents or compliance audits.

### Logged Information

Each audit log entry captures:

| Field | Description | Example |
|-------|-------------|---------|
| `timestamp` | When the operation occurred | `2026-01-06T21:00:00Z` |
| `principalEmail` | Who performed the operation | `therapist@example.com` |
| `callerIp` | IP address of the caller | `203.0.113.42` |
| `serviceName` | GCP service | `firestore.googleapis.com` |
| `methodName` | Firestore operation | `CreateDocument`, `UpdateDocument`, `DeleteDocument` |
| `resourceName` | Document path | `projects/PROJECT/databases/(default)/documents/patients/PATIENT_ID` |
| `status` | Operation result | `success` or error details |
| `request` | Request metadata | Resource identifiers (no PHI content) |

**Important**: Audit logs **never include PHI data contents**. They only capture metadata about operations (who, what, when, where).

### Firestore Operations Logged

**DATA_WRITE operations** (currently enabled):
- `CreateDocument` - New patient/session created
- `UpdateDocument` - Patient/session modified
- `DeleteDocument` - Patient/session deleted
- `Commit` - Batch write committed
- `BatchWrite` - Batch operation executed

**ADMIN_READ operations** (currently enabled):
- `ListCollectionIds` - Collections listed
- `RunQuery` - Query metadata accessed

**DATA_READ operations** (disabled for cost):
- `GetDocument` - Individual document read
- `BatchGetDocuments` - Batch document read
- `RunQuery` - Query execution (with results)
- `Listen` - Real-time listener attached

**Admin Activity operations** (always enabled, free):
- `CreateDatabase`, `UpdateDatabase`, `DeleteDatabase`
- `CreateIndex`, `UpdateField`, `DeleteIndex`
- `ExportDocuments`, `ImportDocuments`
- `CreateBackup`, `RestoreDatabase`

---

## Enabling Audit Logs

### Prerequisites

1. **gcloud CLI installed**: [Install gcloud](https://cloud.google.com/sdk/docs/install)
2. **Authenticated**: Run `gcloud auth login`
3. **Required IAM role**: Project Owner or Security Admin
4. **Python 3 with PyYAML**: Required for the setup script
   ```bash
   pip3 install pyyaml
   ```

### Step-by-Step Setup

#### 1. Set Your GCP Project ID

```bash
export GCP_PROJECT_ID=your-gcp-project-id
```

#### 2. Run the Setup Script

```bash
cd backend/scripts
./enable-audit-logs.sh
```

The script will:
1. Fetch your current IAM policy
2. Check for existing audit configurations
3. Add audit logging for `datastore.googleapis.com` (Firestore)
4. Show you the changes before applying
5. Apply the updated IAM policy
6. Verify the configuration

#### 3. Verify Audit Logs Are Enabled

```bash
# Check IAM policy
gcloud projects get-iam-policy $GCP_PROJECT_ID --format=yaml | grep -A 10 datastore

# Expected output:
# auditConfigs:
# - auditLogConfigs:
#   - logType: ADMIN_READ
#   - logType: DATA_WRITE
#   service: datastore.googleapis.com
```

#### 4. Test Log Generation

Create a test document in Firestore:

```bash
# Using gcloud
gcloud firestore documents create --collection=test --document-id=audit-test \
  --database='(default)' \
  --project=$GCP_PROJECT_ID \
  --data='{"test": "audit logging"}'
```

Wait 1-2 minutes for logs to propagate, then verify:

```bash
gcloud logging read 'protoPayload.serviceName="firestore.googleapis.com"' \
  --limit=10 \
  --format=json
```

You should see a `CreateDocument` log entry.

#### 5. Clean Up Test Document

```bash
gcloud firestore documents delete test/audit-test \
  --database='(default)' \
  --project=$GCP_PROJECT_ID
```

---

## Log Retention Strategy

HIPAA requires audit logs to be retained for **6 years**. GCP Cloud Logging provides 30-day retention by default. To meet HIPAA requirements:

### Short-term Storage (30 days) - Cloud Logging

- **Purpose**: Real-time monitoring, alerting, incident investigation
- **Cost**: ~$0.50/GB ingestion + $0.01/GB/month storage
- **Query method**: Cloud Logging UI or `gcloud logging read`

### Long-term Storage (6 years) - Cloud Storage

- **Purpose**: HIPAA compliance, historical analysis, forensic investigation
- **Cost**: ~$0.020/GB/month (Nearline) or $0.004/GB/month (Coldline)
- **Implementation**: Create a log sink to export to Cloud Storage

**To set up long-term retention**, see the follow-up task: "Implement audit log export to Cloud Storage for 6-year HIPAA retention"

### Recommended Retention Tiers

| Storage | Retention Period | Use Case | Cost/GB/month |
|---------|------------------|----------|---------------|
| **Cloud Logging** | 30 days | Active monitoring, alerts | $0.01 |
| **Cloud Storage (Nearline)** | 1-6 months | Recent investigation | $0.020 |
| **Cloud Storage (Coldline)** | 6 months - 6 years | HIPAA compliance | $0.004 |
| **Cloud Storage (Archive)** | 6+ years | Legal hold | $0.0012 |

---

## Querying and Monitoring Logs

### Basic Queries

#### View all Firestore audit logs (last hour)

```bash
gcloud logging read 'protoPayload.serviceName="firestore.googleapis.com"' \
  --limit=50 \
  --format=json \
  --freshness=1h
```

#### View DATA_WRITE operations only

```bash
gcloud logging read '
  protoPayload.serviceName="firestore.googleapis.com"
  AND protoPayload.methodName=~"(CreateDocument|UpdateDocument|DeleteDocument|Commit)"
' --limit=50
```

#### View operations by specific user

```bash
gcloud logging read '
  protoPayload.serviceName="firestore.googleapis.com"
  AND protoPayload.authenticationInfo.principalEmail="therapist@example.com"
' --limit=50
```

#### View operations on specific patient

```bash
gcloud logging read '
  protoPayload.serviceName="firestore.googleapis.com"
  AND protoPayload.resourceName=~"patients/PATIENT_ID"
' --limit=50
```

#### View failed operations

```bash
gcloud logging read '
  protoPayload.serviceName="firestore.googleapis.com"
  AND protoPayload.status.code!=0
' --limit=50
```

### Cloud Console Queries

Navigate to: **Cloud Console → Logging → Logs Explorer**

Use these filters:

```
resource.type="cloud_firestore_database"
protoPayload.serviceName="firestore.googleapis.com"
```

Add filters for specific operations:
- **methodName**: `CreateDocument`, `UpdateDocument`, `DeleteDocument`
- **principalEmail**: `user@example.com`
- **resourceName**: `projects/.../documents/patients/...`

### Example Queries for Common Scenarios

#### 1. Who accessed this patient's record today?

```
resource.type="cloud_firestore_database"
protoPayload.serviceName="firestore.googleapis.com"
protoPayload.resourceName=~"patients/PATIENT_ID"
timestamp >= "2026-01-06T00:00:00Z"
```

#### 2. All deletions in the last 7 days

```
resource.type="cloud_firestore_database"
protoPayload.serviceName="firestore.googleapis.com"
protoPayload.methodName="DeleteDocument"
timestamp >= "2025-12-30T00:00:00Z"
```

#### 3. Unusual access patterns (after hours)

```
resource.type="cloud_firestore_database"
protoPayload.serviceName="firestore.googleapis.com"
timestamp >= "2026-01-06T22:00:00Z"  -- After 10 PM
timestamp < "2026-01-07T06:00:00Z"   -- Before 6 AM
```

---

## Cost Optimization

### Current Configuration Cost Estimate

**Configuration**: ADMIN_READ + DATA_WRITE only (DATA_READ disabled)

| Component | Volume (small practice) | Cost/month |
|-----------|-------------------------|------------|
| Log ingestion (DATA_WRITE) | 100-500 MB | $0.05-$0.25 |
| Log storage (30 days) | 100-500 MB | $0.001-$0.005 |
| Log ingestion (ADMIN_READ) | 10-50 MB | $0.01-$0.03 |
| **Total** | **~150-600 MB** | **$1-5** |

### If DATA_READ Is Enabled

| Component | Volume (small practice) | Cost/month |
|-----------|-------------------------|------------|
| Log ingestion (DATA_READ) | 1-5 GB | $0.50-$2.50 |
| Log storage (30 days) | 1-5 GB | $0.01-$0.05 |
| Combined with DATA_WRITE | +100-500 MB | +$0.05-$0.25 |
| **Total** | **~1.5-6 GB** | **$20-100** |

### Cost Reduction Strategies

#### 1. Use Exclusion Filters

Filter out non-PHI or routine operations:

```bash
# Exclude health check queries
gcloud logging sinks create exclude-health-checks \
  storage.googleapis.com/audit-logs-bucket \
  --log-filter='
    protoPayload.serviceName="firestore.googleapis.com"
    NOT protoPayload.resourceName=~"health"
  '
```

#### 2. Export to Cheaper Storage

Instead of keeping all logs in Cloud Logging ($0.50/GB ingestion):
- Export to Cloud Storage Coldline ($0.004/GB/month)
- Saves ~99% on storage costs for long-term retention

#### 3. Sampling (DATA_READ only)

For DATA_READ logs, sample a percentage:

```bash
# Log only 10% of read operations
--log-filter='
  protoPayload.serviceName="firestore.googleapis.com"
  AND protoPayload.methodName="GetDocument"
  AND sample(insertId, 0.1)
'
```

**Warning**: Sampling reduces audit trail completeness. Only use for DATA_READ in low-risk environments.

#### 4. Separate Log Sinks

Create different sinks for different log types:
- **Critical logs (DATA_WRITE)**: Keep all, export to Cloud Storage immediately
- **Informational logs (ADMIN_READ)**: Keep 7 days, then archive
- **Verbose logs (DATA_READ if enabled)**: Sample or filter heavily

---

## Incident Investigation

### Common Investigation Scenarios

#### Scenario 1: Unauthorized Access

**Question**: "Did anyone access patient John Doe's record outside business hours?"

```bash
gcloud logging read '
  protoPayload.serviceName="firestore.googleapis.com"
  AND protoPayload.resourceName=~"patients/PATIENT_ID"
  AND (timestamp < "2026-01-06T08:00:00Z" OR timestamp > "2026-01-06T18:00:00Z")
' --limit=100 --format=json
```

**Look for**:
- Unknown `principalEmail` addresses
- Unexpected `callerIp` addresses
- Failed access attempts (`status.code != 0`)

#### Scenario 2: Data Modification Audit

**Question**: "Who modified this patient's diagnosis on January 5th?"

```bash
gcloud logging read '
  protoPayload.serviceName="firestore.googleapis.com"
  AND protoPayload.methodName="UpdateDocument"
  AND protoPayload.resourceName=~"patients/PATIENT_ID"
  AND timestamp >= "2026-01-05T00:00:00Z"
  AND timestamp < "2026-01-06T00:00:00Z"
' --format=json
```

**Extract**:
- `principalEmail`: Who made the change
- `timestamp`: Exact time of modification
- `callerIp`: Where they were connecting from
- `requestMetadata.callerSuppliedUserAgent`: What client they used

#### Scenario 3: Bulk Deletion Investigation

**Question**: "Were any patients deleted in the last month?"

```bash
gcloud logging read '
  protoPayload.serviceName="firestore.googleapis.com"
  AND protoPayload.methodName="DeleteDocument"
  AND protoPayload.resourceName=~"patients/"
  AND timestamp >= "2025-12-06T00:00:00Z"
' --format=json
```

#### Scenario 4: Suspicious Activity Pattern

**Question**: "Has this user account been accessing more records than usual?"

```bash
# Get all operations by user in the last 24 hours
gcloud logging read '
  protoPayload.serviceName="firestore.googleapis.com"
  AND protoPayload.authenticationInfo.principalEmail="suspicious@example.com"
  AND timestamp >= "2026-01-05T00:00:00Z"
' --format=json | jq length
```

Compare the count to the user's normal baseline.

### Log Analysis with BigQuery

For complex investigations, export logs to BigQuery:

```sql
-- Find users who accessed more than 50 patient records today
SELECT
  protopayload_auditlog.authenticationInfo.principalEmail,
  COUNT(DISTINCT REGEXP_EXTRACT(protopayload_auditlog.resourceName, 'patients/([^/]+)')) as patient_count
FROM
  `project.dataset.cloudaudit_googleapis_com_data_access`
WHERE
  DATE(timestamp) = CURRENT_DATE()
  AND protopayload_auditlog.serviceName = 'firestore.googleapis.com'
GROUP BY
  protopayload_auditlog.authenticationInfo.principalEmail
HAVING
  patient_count > 50
ORDER BY
  patient_count DESC
```

---

## Enabling DATA_READ Logs

If you need to enable DATA_READ logs later (for security incidents, audits, or enhanced monitoring):

### Update the Audit Configuration

Edit the IAM policy file:

```bash
# Get current policy
gcloud projects get-iam-policy $GCP_PROJECT_ID --format=yaml > policy.yaml

# Edit policy.yaml and add DATA_READ under auditLogConfigs:
# auditConfigs:
# - auditLogConfigs:
#   - logType: ADMIN_READ
#   - logType: DATA_WRITE
#   - logType: DATA_READ        # Add this line
#   service: datastore.googleapis.com

# Apply updated policy
gcloud projects set-iam-policy $GCP_PROJECT_ID policy.yaml
```

### Cost Impact

Enabling DATA_READ will increase costs by ~20-100x depending on read volume:
- **Current**: $1-5/month
- **With DATA_READ**: $20-100/month for small-medium practice

### Mitigation Strategies

1. **Enable for limited time**: Turn on for incident investigation, then disable
2. **Use exclusion filters**: Filter out non-PHI collections or routine queries
3. **Sample reads**: Log only 10-20% of read operations
4. **Export to cheaper storage**: Immediately send to Cloud Storage instead of Cloud Logging

---

## Monitoring and Alerts

### Recommended Alerts

Set up Cloud Monitoring alerts for:

#### 1. Failed Operations

Alert when Firestore operations fail:

```bash
gcloud alpha monitoring policies create \
  --notification-channels=CHANNEL_ID \
  --display-name="Firestore Failed Operations" \
  --condition-display-name="Firestore errors detected" \
  --condition-threshold-value=10 \
  --condition-threshold-duration=300s \
  --filter='
    resource.type="cloud_firestore_database"
    AND protoPayload.status.code!=0
  '
```

#### 2. After-Hours Access

Alert on access outside business hours:

```bash
# Create a log-based metric for after-hours access
gcloud logging metrics create after_hours_access \
  --description="Firestore access after hours" \
  --log-filter='
    protoPayload.serviceName="firestore.googleapis.com"
    AND (HOUR(timestamp) < 6 OR HOUR(timestamp) > 22)
  '

# Create alert on the metric
gcloud alpha monitoring policies create \
  --notification-channels=CHANNEL_ID \
  --display-name="After Hours Database Access" \
  --condition-display-name="Access detected after hours" \
  --condition-threshold-value=1 \
  --condition-threshold-duration=60s
```

#### 3. Bulk Deletions

Alert on multiple deletions in short time:

```bash
gcloud logging metrics create bulk_deletions \
  --description="Multiple Firestore deletions" \
  --log-filter='
    protoPayload.serviceName="firestore.googleapis.com"
    AND protoPayload.methodName="DeleteDocument"
  '

# Alert if >5 deletions in 5 minutes
gcloud alpha monitoring policies create \
  --notification-channels=CHANNEL_ID \
  --display-name="Bulk Deletion Detected" \
  --condition-threshold-value=5 \
  --condition-threshold-duration=300s
```

---

## HIPAA Compliance Checklist

- [x] Audit controls implemented (§ 164.312(b))
- [x] Track all PHI modifications (DATA_WRITE enabled)
- [x] Capture user identity (principalEmail)
- [x] Timestamp all operations
- [x] Protect log integrity (Cloud Logging IAM controls)
- [ ] 6-year log retention (requires Cloud Storage export - see follow-up task)
- [ ] Regular log review process documented
- [ ] Incident response procedures documented
- [ ] Staff training on audit log access

---

## References

- [HIPAA Security Rule](https://www.hhs.gov/hipaa/for-professionals/security/index.html)
- [GCP Cloud Audit Logs Overview](https://docs.cloud.google.com/logging/docs/audit)
- [Firestore Audit Logging](https://docs.cloud.google.com/firestore/docs/audit-logging)
- [Enable Data Access Audit Logs](https://cloud.google.com/logging/docs/audit/configure-data-access)
- [HIPAA Compliance on GCP](https://cloud.google.com/security/compliance/hipaa)
- [Cloud Logging Pricing](https://cloud.google.com/logging/pricing)

---

**Last Updated**: 2026-03-19
**Maintained By**: Pablo Health, LLC
**Related Documents**: `HIPAA_SECURITY.md`
