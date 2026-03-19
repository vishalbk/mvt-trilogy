output "pubsub_signals_topic" {
  description = "Pub/Sub topic for cross-cloud event relay"
  value       = module.pubsub.signals_topic_name
}

output "pubsub_alerts_topic" {
  description = "Pub/Sub topic for high-priority alerts"
  value       = module.pubsub.alerts_topic_name
}

output "pubsub_analytics_topic" {
  description = "Pub/Sub topic for BigQuery analytics events"
  value       = module.pubsub.analytics_topic_name
}

output "bigquery_dataset_id" {
  description = "BigQuery dataset ID for analytics"
  value       = module.bigquery.dataset_id
}

output "bigquery_project_id" {
  description = "BigQuery project ID"
  value       = module.bigquery.project_id
}

output "firestore_database" {
  description = "Firestore database name"
  value       = module.firestore.database_name
}

# output "signal_processor_function_url" {
#   description = "HTTP trigger URL for signal-processor Cloud Function"
#   value       = module.functions.signal_processor_url
# }
#
# output "analytics_writer_function_url" {
#   description = "HTTP trigger URL for analytics-writer Cloud Function"
#   value       = module.functions.analytics_writer_url
# }
#
# output "gdelt_extractor_function_url" {
#   description = "HTTP trigger URL for gdelt-extractor Cloud Function"
#   value       = module.functions.gdelt_extractor_url
# }

output "hosting_bucket_url" {
  description = "GCS static hosting URL for analytics dashboard"
  value       = "https://storage.googleapis.com/${var.project_id}-mvt-dashboard-${var.environment}/index.html"
}

output "monitoring_dashboard_url" {
  description = "Cloud Monitoring dashboard URL"
  value       = "https://console.cloud.google.com/monitoring/dashboards/custom/mvt-observatory-gcp?project=${var.project_id}"
}
