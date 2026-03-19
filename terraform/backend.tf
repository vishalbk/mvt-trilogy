# For local development, uncomment the lines below and comment out the gcs backend in main.tf
# terraform {
#   backend "local" {
#     path = "./terraform.tfstate"
#   }
# }

# For production/staging, use GCS backend (configured in main.tf)
# Run: gsutil mb gs://mvt-trilogy-terraform-state
# Then: terraform init -backend-config="bucket=mvt-trilogy-terraform-state"
