variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "environment" {
  type = string
}

variable "pubsub_signals_topic" {
  type = string
}

variable "pubsub_analytics_topic" {
  type = string
}

variable "bigquery_dataset_id" {
  type = string
}

variable "firestore_database" {
  type = string
}

variable "aws_event_relay_endpoint" {
  type      = string
  sensitive = true
}

locals {
  runtime      = "python312"
  memory_mb    = 256
  timeout      = 60
  min_instances = 0
  max_instances = 5
}

# Service account for Cloud Functions
resource "google_service_account" "cloud_functions_sa" {
  account_id   = "mvt-cloud-functions"
  project      = var.project_id
  display_name = "MVT Cloud Functions Service Account"
}

# IAM bindings for Cloud Functions SA
# Note: These require Cloud Resource Manager API to be enabled
# Commented out until API is available
# resource "google_project_iam_member" "functions_pubsub_editor" {
#   project = var.project_id
#   role    = "roles/pubsub.editor"
#   member  = "serviceAccount:${google_service_account.cloud_functions_sa.email}"
# }
# resource "google_project_iam_member" "functions_bigquery_editor" {
#   project = var.project_id
#   role    = "roles/bigquery.dataEditor"
#   member  = "serviceAccount:${google_service_account.cloud_functions_sa.email}"
# }
# resource "google_project_iam_member" "functions_bigquery_user" {
#   project = var.project_id
#   role    = "roles/bigquery.user"
#   member  = "serviceAccount:${google_service_account.cloud_functions_sa.email}"
# }
# resource "google_project_iam_member" "functions_firestore_user" {
#   project = var.project_id
#   role    = "roles/datastore.user"
#   member  = "serviceAccount:${google_service_account.cloud_functions_sa.email}"
# }
# resource "google_project_iam_member" "functions_cloud_run_invoker" {
#   project = var.project_id
#   role    = "roles/run.invoker"
#   member  = "serviceAccount:${google_service_account.cloud_functions_sa.email}"
# }

# Cloud Functions require Eventarc API to be enabled - Commenting out until API is available
# signal_processor, analytics_writer, and gdelt_extractor functions

# Cloud Scheduler trigger for GDELT Extractor (every 15 minutes)
# Commented out until cloud functions are deployed
# resource "google_cloud_scheduler_job" "gdelt_extraction_schedule" {
#   name            = "gdelt-extraction-trigger"
#   project         = var.project_id
#   region          = var.region
#   description     = "Triggers GDELT extraction every 15 minutes"
#   schedule        = "*/15 * * * *"
#   time_zone       = "UTC"
#   attempt_deadline = "320s"
#
#   http_target {
#     http_method = "POST"
#     uri         = google_cloudfunctions2_function.gdelt_extractor.service_config[0].uri
#
#     oidc_token {
#       service_account_email = google_service_account.cloud_functions_sa.email
#     }
#   }
# }

# Cloud Storage bucket for function source code
resource "google_storage_bucket" "functions_source" {
  name          = "${var.project_id}-functions-source"
  project       = var.project_id
  location      = "US"
  force_destroy = false

  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  labels = {
    environment = var.environment
  }
}

# Placeholder objects - in reality these would be zip files with function code
resource "google_storage_bucket_object" "signal_processor_source" {
  name   = "signal-processor-source.zip"
  bucket = google_storage_bucket.functions_source.name
  source = "${path.module}/src/signal-processor/main.py"
}

resource "google_storage_bucket_object" "analytics_writer_source" {
  name   = "analytics-writer-source.zip"
  bucket = google_storage_bucket.functions_source.name
  source = "${path.module}/src/analytics-writer/main.py"
}

resource "google_storage_bucket_object" "gdelt_extractor_source" {
  name   = "gdelt-extractor-source.zip"
  bucket = google_storage_bucket.functions_source.name
  source = "${path.module}/src/gdelt-extractor/main.py"
}

# outputs commented out since functions are not deployed
# output "signal_processor_url" {
#   value       = google_cloudfunctions2_function.signal_processor.service_config[0].uri
#   description = "HTTP trigger URL for signal-processor function"
# }
#
# output "analytics_writer_url" {
#   value       = google_cloudfunctions2_function.analytics_writer.service_config[0].uri
#   description = "HTTP trigger URL for analytics-writer function"
# }
#
# output "gdelt_extractor_url" {
#   value       = google_cloudfunctions2_function.gdelt_extractor.service_config[0].uri
#   description = "HTTP trigger URL for gdelt-extractor function"
# }
