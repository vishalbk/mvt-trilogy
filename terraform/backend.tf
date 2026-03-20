# For local development, uncomment the lines below and comment out the gcs backend in main.tf
# terraform {
#   backend "local" {
#     path = "./terraform.tfstate"
#   }
# }

# For production/staging, use GCS backend (configured in main.tf)
# Run: gsutil mb gs://mvt-trilogy-terraform-state
# Then: terraform init -backend-config="bucket=mvt-trilogy-terraform-state"
# Terraform state imported and lock released (2026-03-20T04:15:00Z)
# All 16 GCP resources imported into state — ready for clean apply
