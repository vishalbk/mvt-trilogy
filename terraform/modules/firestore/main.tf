variable "project_id" {
  type = string
}

variable "environment" {
  type = string
}

variable "skip_app_engine_init" {
  type        = bool
  description = "Skip App Engine initialization (set to true if already exists)"
  default     = false
}

resource "google_firestore_database" "mvt" {
  project     = var.project_id
  name        = "(default)"
  location_id = "nam5"
  type        = "FIRESTORE_NATIVE"
}

resource "google_app_engine_application" "app" {
  count         = var.skip_app_engine_init ? 0 : 1
  project       = var.project_id
  location_id   = "us-central"
  database_type = "CLOUD_FIRESTORE"
}

# Note: Index documents are created through google_firestore_index resources below
# Collections will be created automatically when data is written to them

# Composite indexes already exist and will be created through Firestore console
# Commenting out to avoid "index already exists" error
# resource "google_firestore_index" "dashboard_signals_queries" {
#   project    = var.project_id
#   database   = google_firestore_database.mvt.name
#   collection = "dashboard_signals"
#
#   fields {
#     field_path = "dashboard_id"
#     order      = "ASCENDING"
#   }
#
#   fields {
#     field_path = "timestamp"
#     order      = "DESCENDING"
#   }
# }
#
# resource "google_firestore_index" "sentiment_time_range" {
#   project    = var.project_id
#   database   = google_firestore_database.mvt.name
#   collection = "processed_analytics"
#
#   fields {
#     field_path = "event_type"
#     order      = "ASCENDING"
#   }
#
#   fields {
#     field_path = "date"
#     order      = "DESCENDING"
#   }
# }

output "database_name" {
  value = google_firestore_database.mvt.name
}

output "app_engine_app" {
  value = var.skip_app_engine_init ? null : google_app_engine_application.app[0].id
}
