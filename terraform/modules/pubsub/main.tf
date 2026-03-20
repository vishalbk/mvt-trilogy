variable "project_id" {
  type = string
}

variable "environment" {
  type = string
}

locals {
  topic_config = {
    signals = {
      topic_id     = "mvt-signals"
      subscription = "mvt-signals-sub"
      dlq_topic    = "mvt-signals-dlq"
      dlq_sub      = "mvt-signals-dlq-sub"
      description  = "Cross-cloud event relay from AWS EventBridge"
    }
    alerts = {
      topic_id     = "mvt-alerts"
      subscription = "mvt-alerts-sub"
      dlq_topic    = "mvt-alerts-dlq"
      dlq_sub      = "mvt-alerts-dlq-sub"
      description  = "High-priority alerts for Telegram notification"
    }
    analytics = {
      topic_id     = "mvt-analytics"
      subscription = "mvt-analytics-sub"
      dlq_topic    = "mvt-analytics-dlq"
      dlq_sub      = "mvt-analytics-dlq-sub"
      description  = "Events destined for BigQuery analytics"
    }
  }
}

# Signals topic (cross-cloud relay from AWS)
resource "google_pubsub_topic" "signals" {
  name    = local.topic_config.signals.topic_id
  project = var.project_id
  message_storage_policy {
    allowed_persistence_regions = ["us-central1"]
  }
}

resource "google_pubsub_topic" "signals_dlq" {
  name    = local.topic_config.signals.dlq_topic
  project = var.project_id
  message_storage_policy {
    allowed_persistence_regions = ["us-central1"]
  }
}

resource "google_pubsub_subscription" "signals" {
  name                 = local.topic_config.signals.subscription
  project              = var.project_id
  topic                = google_pubsub_topic.signals.name
  ack_deadline_seconds = 60

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.signals_dlq.id
    max_delivery_attempts = 5
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
}

resource "google_pubsub_subscription" "signals_dlq" {
  name                 = local.topic_config.signals.dlq_sub
  project              = var.project_id
  topic                = google_pubsub_topic.signals_dlq.name
  ack_deadline_seconds = 300
}

# Alerts topic (high-priority for Telegram)
resource "google_pubsub_topic" "alerts" {
  name    = local.topic_config.alerts.topic_id
  project = var.project_id
  message_storage_policy {
    allowed_persistence_regions = ["us-central1"]
  }
}

resource "google_pubsub_topic" "alerts_dlq" {
  name    = local.topic_config.alerts.dlq_topic
  project = var.project_id
  message_storage_policy {
    allowed_persistence_regions = ["us-central1"]
  }
}

resource "google_pubsub_subscription" "alerts" {
  name                 = local.topic_config.alerts.subscription
  project              = var.project_id
  topic                = google_pubsub_topic.alerts.name
  ack_deadline_seconds = 60

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.alerts_dlq.id
    max_delivery_attempts = 5
  }
}

resource "google_pubsub_subscription" "alerts_dlq" {
  name                 = local.topic_config.alerts.dlq_sub
  project              = var.project_id
  topic                = google_pubsub_topic.alerts_dlq.name
  ack_deadline_seconds = 300
}

# Analytics topic (BigQuery events)
resource "google_pubsub_topic" "analytics" {
  name    = local.topic_config.analytics.topic_id
  project = var.project_id
  message_storage_policy {
    allowed_persistence_regions = ["us-central1"]
  }
}

resource "google_pubsub_topic" "analytics_dlq" {
  name    = local.topic_config.analytics.dlq_topic
  project = var.project_id
  message_storage_policy {
    allowed_persistence_regions = ["us-central1"]
  }
}

resource "google_pubsub_subscription" "analytics" {
  name                 = local.topic_config.analytics.subscription
  project              = var.project_id
  topic                = google_pubsub_topic.analytics.name
  ack_deadline_seconds = 300

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.analytics_dlq.id
    max_delivery_attempts = 5
  }
}

resource "google_pubsub_subscription" "analytics_dlq" {
  name                 = local.topic_config.analytics.dlq_sub
  project              = var.project_id
  topic                = google_pubsub_topic.analytics_dlq.name
  ack_deadline_seconds = 300
}

output "signals_topic_name" {
  value = google_pubsub_topic.signals.name
}

output "alerts_topic_name" {
  value = google_pubsub_topic.alerts.name
}

output "analytics_topic_name" {
  value = google_pubsub_topic.analytics.name
}
