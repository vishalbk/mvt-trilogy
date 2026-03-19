# MVT Trilogy - Deployment Guide

## Quick Start

### 1. Prerequisites

```bash
# Install tools
brew install terraform aws-cdk gcloud

# GCP setup
gcloud auth login
gcloud config set project mvt-trilogy-dev

# Verify Terraform
terraform version  # Should be >= 1.0
```

### 2. Initialize Terraform

```bash
cd terraform

# For development (local state)
terraform init

# For staging/production (GCS state)
terraform init -backend-config="bucket=mvt-trilogy-terraform-state" \
               -backend-config="prefix=staging"

# Verify configuration
terraform fmt -check -recursive
terraform validate
```

### 3. Deploy to Development

```bash
# Plan changes
terraform plan -var-file="environments/dev.tfvars" \
               -var="aws_event_relay_endpoint=https://events.us-east-1.amazonaws.com/" \
               -out=tfplan

# Apply changes
terraform apply tfplan

# Get outputs
terraform output -json > outputs.json
```

### 4. Deploy Cloud Functions

The Terraform modules create Cloud Function resources, but you need to prepare the source code:

```bash
# Package function source
cd terraform/modules/functions/src/signal-processor
zip -r signal-processor.zip .
gsutil cp signal-processor.zip gs://mvt-trilogy-dev-functions-source/

# Repeat for other functions
cd ../analytics-writer
zip -r analytics-writer.zip .
gsutil cp analytics-writer.zip gs://mvt-trilogy-dev-functions-source/

cd ../gdelt-extractor
zip -r gdelt-extractor.zip .
gsutil cp gdelt-extractor.zip gs://mvt-trilogy-dev-functions-source/
```

### 5. Set GitHub Secrets

```bash
# Create .env.local with secrets (do not commit)
cat > .env.local << EOF
AWS_ACCESS_KEY_ID=xxx
AWS_SECRET_ACCESS_KEY=xxx
GCP_WORKLOAD_IDENTITY_PROVIDER=xxx
GCP_SERVICE_ACCOUNT=xxx
GCP_PROJECT_ID_STAGING=mvt-trilogy-staging
GCP_PROJECT_ID_PROD=mvt-trilogy-prod
GCP_TERRAFORM_BUCKET=mvt-trilogy-terraform-state
JIRA_API_TOKEN=xxx
JIRA_ISSUE_URL=https://jira.company.com/browse/MVT-1
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx
AWS_EVENT_RELAY_ENDPOINT=https://events.us-east-1.amazonaws.com/
EOF

# Add secrets to GitHub (via CLI or web interface)
gh secret set AWS_ACCESS_KEY_ID < ~/.aws/credentials
gh secret set GCP_WORKLOAD_IDENTITY_PROVIDER --body "projects/123/locations/global/workloadIdentityPools/xxx"
# ... repeat for all secrets
```

### 6. Test PR Checks Locally

```bash
# Run linting
npm run lint:js
flake8 . --count --select=E9,F63,F7,F82

# Run tests
npm run test:unit
pytest tests/unit/ -v

# Validate Terraform
cd terraform
terraform fmt -check -recursive
terraform validate

# CDK validation
cd ../cdk
npm run build
cdk synth
```

### 7. Deploy to Staging (via GitHub)

```bash
# Create a pull request
git checkout -b feature/my-change
git commit -m "My changes"
git push origin feature/my-change

# Open PR on GitHub - pr-checks.yml will run automatically

# Once PR is merged to main
# deploy-staging.yml triggers automatically
# Check Actions tab for progress
```

### 8. Promote to Canary

Canary deployment triggers automatically when staging succeeds:
- 10% traffic to new version for 5 minutes
- Monitors CloudWatch (AWS) and Cloud Monitoring (GCP)
- Checks error rates and latency
- Auto-rolls back if unhealthy

### 9. Promote to Production

```bash
# Option 1: GitHub Web UI
# Go to Actions → Deploy to Production → Run workflow
# Input: confirm=yes

# Option 2: CLI
gh workflow run deploy-production.yml -f confirm=yes

# Production deployment requires:
# - Passing smoke tests
# - JIRA evidence attachment
# - Telegram notification
# - GitHub Release creation
```

## Terraform Modules Reference

### Pub/Sub Module
```hcl
module "pubsub" {
  source      = "./modules/pubsub"
  project_id  = var.project_id
  environment = var.environment
}

# Outputs:
# - signals_topic_name
# - alerts_topic_name
# - analytics_topic_name
```

### BigQuery Module
```hcl
module "bigquery" {
  source      = "./modules/bigquery"
  project_id  = var.project_id
  environment = var.environment
}

# Outputs:
# - dataset_id
# - tables (object with all table IDs)
# - project_id
```

### Cloud Functions Module
```hcl
module "functions" {
  source                  = "./modules/functions"
  project_id              = var.project_id
  region                  = var.region
  environment             = var.environment
  pubsub_signals_topic    = module.pubsub.signals_topic_name
  pubsub_analytics_topic  = module.pubsub.analytics_topic_name
  bigquery_dataset_id     = module.bigquery.dataset_id
  firestore_database      = module.firestore.database_name
  aws_event_relay_endpoint = var.aws_event_relay_endpoint
}

# Outputs:
# - signal_processor_url
# - analytics_writer_url
# - gdelt_extractor_url
```

### Firestore Module
```hcl
module "firestore" {
  source      = "./modules/firestore"
  project_id  = var.project_id
  environment = var.environment
}

# Outputs:
# - database_name
# - app_engine_app
```

### Hosting Module
```hcl
module "hosting" {
  source      = "./modules/hosting"
  project_id  = var.project_id
  environment = var.environment
}

# Outputs:
# - hosting_url
# - site_id
```

### Monitoring Module
```hcl
module "monitoring" {
  source      = "./modules/monitoring"
  project_id  = var.project_id
  environment = var.environment
  region      = var.region
}

# Outputs:
# - dashboard_url
# - notification_channel_email
```

## Monitoring & Debugging

### Check Pub/Sub Topics
```bash
# List topics
gcloud pubsub topics list

# Check subscription backlog
gcloud pubsub subscriptions describe mvt-signals-sub

# Test publish
gcloud pubsub topics publish mvt-signals \
  --message='{"test": "message"}'

# Pull messages
gcloud pubsub subscriptions pull mvt-signals-sub --auto-ack --limit=5
```

### Check Cloud Functions
```bash
# List functions
gcloud functions list

# View logs
gcloud functions logs read signal-processor --limit=10

# Test function via HTTP trigger
curl -X POST https://us-central1-mvt-trilogy-dev.cloudfunctions.net/signal-processor \
  -H "Content-Type: application/json" \
  -d '{"test": "data"}'
```

### Check BigQuery
```bash
# List tables
bq ls mvt_analytics

# Query recent data
bq query --use_legacy_sql=false "
  SELECT * FROM mvt_analytics.inequality_signals
  LIMIT 10
"

# Check table schema
bq show --schema mvt_analytics.inequality_signals
```

### Check Firestore
```bash
# View database
gcloud firestore databases list

# Check collections
gcloud firestore collections list

# View documents
gcloud firestore documents list dashboard_signals
```

### View Monitoring Dashboard
```bash
# Open Cloud Monitoring
gcloud monitoring dashboards list
# Copy dashboard ID and open:
# https://console.cloud.google.com/monitoring/dashboards/custom/{id}?project={project_id}
```

## Troubleshooting

### Terraform State Lock
```bash
# If state is locked
terraform force-unlock LOCK_ID

# Clear local cache
rm -rf .terraform
terraform init
```

### Cloud Function Deployment Fails
```bash
# Check IAM permissions
gcloud projects get-iam-policy mvt-trilogy-dev

# Ensure service account has necessary roles
gcloud projects add-iam-policy-binding mvt-trilogy-dev \
  --member=serviceAccount:mvt-cloud-functions@mvt-trilogy-dev.iam.gserviceaccount.com \
  --role=roles/pubsub.editor
```

### BigQuery Quota Exceeded
```bash
# Check quota
gcloud compute project-info describe --project=mvt-trilogy-dev \
  | grep -i quota

# View usage
bq show -j --project_id=mvt-trilogy-dev --job_type=QUERY | head -20
```

### Pub/Sub Dead-Letter Messages
```bash
# Check dead-letter subscription
gcloud pubsub subscriptions pull mvt-signals-dlq --auto-ack --limit=10

# Review message details
# Messages here indicate failures - check function logs
```

## Cleanup

### Destroy Resources
```bash
# Staging
terraform destroy -var-file="environments/dev.tfvars" \
                  -var="environment=staging" \
                  -auto-approve

# Production (requires explicit approval)
terraform destroy -var-file="environments/prod.tfvars" \
                  -var="environment=production"
```

### Clean Up State
```bash
# Remove local state
rm -rf terraform.tfstate terraform.tfstate.backup

# Remove remote state
gsutil -m rm -r gs://mvt-trilogy-terraform-state/
```

## Environment-Specific Notes

### Development
- Local Terraform state (terraform.tfstate)
- Free tier GCP resources where possible
- Limited Cloud Scheduler runs (15 min interval)

### Staging
- GCS backend state
- Same resource configuration as production
- Used for pre-production testing

### Production
- GCS backend state with versioning
- Environment protection rules in GitHub
- Manual approval required for deployment
- Monitoring alerts enabled
