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
  runtime       = "python312"
  memory_mb     = 256
  timeout       = 60
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
resource "google_project_iam_member" "functions_pubsub_editor" {
  project = var.project_id
  role    = "roles/pubsub.editor"
  member  = "serviceAccount:${google_service_account.cloud_functions_sa.email}"
}

resource "google_project_iam_member" "functions_bigquery_editor" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_service_account.cloud_functions_sa.email}"
}

resource "google_project_iam_member" "functions_bigquery_user" {
  project = var.project_id
  role    = "roles/bigquery.user"
  member  = "serviceAccount:${google_service_account.cloud_functions_sa.email}"
}

resource "google_project_iam_member" "functions_firestore_user" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.cloud_functions_sa.email}"
}

resource "google_project_iam_member" "functions_cloud_run_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.cloud_functions_sa.email}"
}

# Create zip archives for Cloud Function source code
data "archive_file" "signal_processor_zip" {
  type        = "zip"
  source_dir  = "${path.module}/src/signal-processor"
  output_path = "${path.module}/.terraform/dist/signal-processor.zip"
}

data "archive_file" "analytics_writer_zip" {
  type        = "zip"
  source_dir  = "${path.module}/src/analytics-writer"
  output_path = "${path.module}/.terraform/dist/analytics-writer.zip"
}

data "archive_file" "gdelt_extractor_zip" {
  type        = "zip"
  source_dir  = "${path.module}/src/gdelt-extractor"
  output_path = "${path.module}/.terraform/dist/gdelt-extractor.zip"
}

# Cloud Function: signal-processor
# Pub/Sub triggered - processes signals from AWS relay, writes to Firestore
resource "google_cloudfunctions2_function" "signal_processor" {
  name        = "mvt-signal-processor"
  location    = var.region
  project     = var.project_id
  description = "Processes signals from AWS relay, writes to Firestore"

  build_config {
    runtime     = local.runtime
    entry_point = "process_signal"
    source {
      storage_source {
        bucket = google_storage_bucket.functions_source.name
        object = google_storage_bucket_object.signal_processor_source.name
      }
    }
  }

  service_config {
    max_instance_count    = local.max_instances
    min_instance_count    = local.min_instances
    available_memory_mb   = local.memory_mb
    timeout_seconds       = local.timeout
    service_account_email = google_service_account.cloud_functions_sa.email
  }

  event_trigger {
    trigger_region = var.region
    event_type     = "google.cloud.pubsub.topic.v1.messagePublished"
    pubsub_topic   = "projects/${var.project_id}/topics/${var.pubsub_signals_topic}"
  }

  depends_on = [
    google_storage_bucket_object.signal_processor_source,
    google_project_iam_member.functions_firestore_user,
    google_project_iam_member.functions_pubsub_editor,
  ]
}

# Cloud Function: analytics-writer
# Pub/Sub triggered - routes events to appropriate BigQuery tables
resource "google_cloudfunctions2_function" "analytics_writer" {
  name        = "mvt-analytics-writer"
  location    = var.region
  project     = var.project_id
  description = "Routes events to BigQuery tables based on event type"

  build_config {
    runtime     = local.runtime
    entry_point = "write_analytics"
    source {
      storage_source {
        bucket = google_storage_bucket.functions_source.name
        object = google_storage_bucket_object.analytics_writer_source.name
      }
    }
  }

  service_config {
    max_instance_count    = local.max_instances
    min_instance_count    = local.min_instances
    available_memory_mb   = local.memory_mb
    timeout_seconds       = local.timeout
    service_account_email = google_service_account.cloud_functions_sa.email
  }

  event_trigger {
    trigger_region = var.region
    event_type     = "google.cloud.pubsub.topic.v1.messagePublished"
    pubsub_topic   = "projects/${var.project_id}/topics/${var.pubsub_analytics_topic}"
  }

  depends_on = [
    google_storage_bucket_object.analytics_writer_source,
    google_project_iam_member.functions_bigquery_editor,
    google_project_iam_member.functions_bigquery_user,
    google_project_iam_member.functions_pubsub_editor,
  ]
}

# Cloud Function: gdelt-extractor
# HTTP triggered (via Cloud Scheduler) - queries BigQuery GDELT, publishes to Pub/Sub
resource "google_cloudfunctions2_function" "gdelt_extractor" {
  name        = "mvt-gdelt-extractor"
  location    = var.region
  project     = var.project_id
  description = "Queries GDELT data from BigQuery and publishes to Pub/Sub"

  build_config {
    runtime     = local.runtime
    entry_point = "extract_gdelt"
    source {
      storage_source {
        bucket = google_storage_bucket.functions_source.name
        object = google_storage_bucket_object.gdelt_extractor_source.name
      }
    }
  }

  service_config {
    max_instance_count    = local.max_instances
    min_instance_count    = local.min_instances
    available_memory_mb   = local.memory_mb
    timeout_seconds       = local.timeout
    service_account_email = google_service_account.cloud_functions_sa.email
  }

  depends_on = [
    google_storage_bucket_object.gdelt_extractor_source,
    google_project_iam_member.functions_bigquery_user,
    google_project_iam_member.functions_pubsub_editor,
  ]
}

# Cloud Scheduler trigger for GDELT Extractor (every 15 minutes)
resource "google_cloud_scheduler_job" "gdelt_extraction_schedule" {
  name             = "gdelt-extraction-trigger"
  project          = var.project_id
  region           = var.region
  description      = "Triggers GDELT extraction every 15 minutes"
  schedule         = "*/15 * * * *"
  time_zone        = "UTC"
  attempt_deadline = "320s"

  http_target {
    http_method = "POST"
    uri         = google_cloudfunctions2_function.gdelt_extractor.service_config[0].uri

    oidc_token {
      service_account_email = google_service_account.cloud_functions_sa.email
    }
  }

  depends_on = [
    google_cloudfunctions2_function.gdelt_extractor,
  ]
}

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

# Upload function source code to GCS
resource "google_storage_bucket_object" "signal_processor_source" {
  name   = "signal-processor-source.zip"
  bucket = google_storage_bucket.functions_source.name
  source = data.archive_file.signal_processor_zip.output_path
}

resource "google_storage_bucket_object" "analytics_writer_source" {
  name   = "analytics-writer-source.zip"
  bucket = google_storage_bucket.functions_source.name
  source = data.archive_file.analytics_writer_zip.output_path
}

resource "google_storage_bucket_object" "gdelt_extractor_source" {
  name   = "gdelt-extractor-source.zip"
  bucket = google_storage_bucket.functions_source.name
  source = data.archive_file.gdelt_extractor_zip.output_path
}

output "signal_processor_url" {
  value       = google_cloudfunctions2_function.signal_processor.service_config[0].uri
  description = "HTTP trigger URL for signal-processor function"
}

output "analytics_writer_url" {
  value       = google_cloudfunctions2_function.analytics_writer.service_config[0].uri
  description = "HTTP trigger URL for analytics-writer function"
}

output "gdelt_extractor_url" {
  value       = google_cloudfunctions2_function.gdelt_extractor.service_config[0].uri
  description = "HTTP trigger URL for gdelt-extractor function"
}
