variable "project_id" {
  type = string
}

variable "environment" {
  type = string
}

variable "region" {
  type = string
}

# Notification channel for alerts
resource "google_monitoring_notification_channel" "email" {
  project      = var.project_id
  display_name = "MVT Alerts Email"
  type         = "email"
  enabled      = true

  labels = {
    email_address = "alerts@mvt-observatory.local"
  }
}

# Dashboard
resource "google_monitoring_dashboard" "mvt_observatory" {
  project        = var.project_id
  dashboard_json = jsonencode({
    displayName = "MVT Observatory - GCP"
    mosaicLayout = {
      columns = 12
      tiles = [
        {
          xPos   = 0
          yPos   = 0
          width  = 6
          height = 4
          widget = {
            title = "Pub/Sub Subscription Backlog"
            xyChart = {
              dataSets = [
                {
                  timeSeriesQuery = {
                    timeSeriesFilter = {
                      filter = "resource.type=\"pubsub_subscription\" AND metric.type=\"pubsub.googleapis.com/subscription/num_unacked_messages\""
                      aggregation = {
                        alignmentPeriod  = "60s"
                        perSeriesAligner = "ALIGN_MAX"
                      }
                    }
                  }
                }
              ]
            }
          }
        },
        {
          xPos   = 6
          yPos   = 0
          width  = 6
          height = 4
          widget = {
            title = "BigQuery Streaming Insert Errors"
            xyChart = {
              dataSets = [
                {
                  timeSeriesQuery = {
                    timeSeriesFilter = {
                      filter = "resource.type=\"bigquery_project\" AND metric.type=\"bigquery.googleapis.com/job/num_failed_insert_requests\""
                      aggregation = {
                        alignmentPeriod  = "60s"
                        perSeriesAligner = "ALIGN_RATE"
                      }
                    }
                  }
                }
              ]
            }
          }
        }
      ]
    }
  })
}

# Alert Policy: Pub/Sub - Commented out until metrics are available
# Alert policies require recent data, so metric type may not be available immediately
# resource "google_monitoring_alert_policy" "pubsub_dlq" {
#   project      = var.project_id
#   display_name = "MVT Pub/Sub Subscription Depth"
#   combiner     = "OR"
#   enabled      = true
#
#   conditions {
#     display_name = "High subscription depth"
#
#     condition_threshold {
#       filter          = "resource.type=\"pubsub_subscription\" AND metric.type=\"pubsub.googleapis.com/subscription/num_unacked_messages\""
#       duration        = "60s"
#       comparison      = "COMPARISON_GT"
#       threshold_value = 100
#
#       aggregations {
#         alignment_period   = "60s"
#         per_series_aligner = "ALIGN_MAX"
#       }
#     }
#   }
#
#   notification_channels = [google_monitoring_notification_channel.email.id]
#
#   documentation {
#     content   = "High unacked messages in subscriptions. Check subscriber health."
#     mime_type = "text/markdown"
#   }
# }

output "notification_channel_email" {
  value = google_monitoring_notification_channel.email.id
}
