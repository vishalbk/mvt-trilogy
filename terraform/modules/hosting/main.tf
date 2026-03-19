variable "project_id" {
  type = string
}

variable "environment" {
  type = string
}

locals {
  bucket_name = "mvt-analytics-dashboard-${var.project_id}"
}

# GCS bucket for static website hosting
resource "google_storage_bucket" "mvt_dashboard" {
  name          = local.bucket_name
  project       = var.project_id
  location      = "US"
  force_destroy = true

  uniform_bucket_level_access = true

  website {
    main_page_suffix = "index.html"
    not_found_page   = "index.html"
  }

  versioning {
    enabled = true
  }

  labels = {
    environment = var.environment
    purpose     = "static-hosting"
  }
}

output "hosting_bucket_name" {
  value       = google_storage_bucket.mvt_dashboard.name
  description = "GCS bucket name for static website hosting"
}

output "hosting_bucket_url" {
  value       = "https://storage.googleapis.com/${google_storage_bucket.mvt_dashboard.name}"
  description = "URL of the GCS bucket for static hosting"
}
