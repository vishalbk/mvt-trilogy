variable "project_id" {
  type = string
}

variable "environment" {
  type = string
}

locals {
  dataset_id = "mvt_analytics"
  tables = {
    inequality_signals = {
      schema_file = "${path.module}/schemas/inequality_signals.json"
      partition   = "date"
      clustering  = ["source"]
    }
    sentiment_events = {
      schema_file = "${path.module}/schemas/sentiment_events.json"
      partition   = "date"
      clustering  = ["event_type"]
    }
    sovereign_indicators = {
      schema_file = "${path.module}/schemas/sovereign_indicators.json"
      partition   = "date"
      clustering  = ["country"]
    }
    gdelt_events = {
      schema_file = "${path.module}/schemas/gdelt_events.json"
      partition   = "date"
      clustering  = ["event_code"]
    }
  }
}

resource "google_bigquery_dataset" "mvt_analytics" {
  dataset_id           = local.dataset_id
  project              = var.project_id
  friendly_name        = "MVT Analytics Dataset"
  description          = "Analytics dataset for Macro Vulnerability Trilogy signals, events, and indicators"
  location             = "US"
  default_table_expiration_ms = null

  labels = {
    environment = var.environment
    project     = "mvt-trilogy"
  }
}

resource "google_bigquery_table" "inequality_signals" {
  dataset_id          = google_bigquery_dataset.mvt_analytics.dataset_id
  table_id            = "inequality_signals"
  project             = var.project_id
  deletion_protection = false

  time_partitioning {
    type          = "DAY"
    field         = "date"
    expiration_ms = null
  }

  clustering = ["source"]

  schema = file(local.tables.inequality_signals.schema_file)

  labels = {
    table_type = "signals"
  }
}

resource "google_bigquery_table" "sentiment_events" {
  dataset_id          = google_bigquery_dataset.mvt_analytics.dataset_id
  table_id            = "sentiment_events"
  project             = var.project_id
  deletion_protection = false

  time_partitioning {
    type          = "DAY"
    field         = "date"
    expiration_ms = null
  }

  clustering = ["event_type"]

  schema = file(local.tables.sentiment_events.schema_file)

  labels = {
    table_type = "events"
  }
}

resource "google_bigquery_table" "sovereign_indicators" {
  dataset_id          = google_bigquery_dataset.mvt_analytics.dataset_id
  table_id            = "sovereign_indicators"
  project             = var.project_id
  deletion_protection = false

  time_partitioning {
    type          = "DAY"
    field         = "date"
    expiration_ms = null
  }

  clustering = ["country"]

  schema = file(local.tables.sovereign_indicators.schema_file)

  labels = {
    table_type = "indicators"
  }
}

resource "google_bigquery_table" "gdelt_events" {
  dataset_id          = google_bigquery_dataset.mvt_analytics.dataset_id
  table_id            = "gdelt_events"
  project             = var.project_id
  deletion_protection = false

  time_partitioning {
    type          = "DAY"
    field         = "date"
    expiration_ms = null
  }

  clustering = ["event_code"]

  schema = file(local.tables.gdelt_events.schema_file)

  labels = {
    table_type = "gdelt"
  }
}

# BigQuery saved queries for analytics
resource "google_bigquery_routine" "gdelt_extraction" {
  project      = var.project_id
  dataset_id   = google_bigquery_dataset.mvt_analytics.dataset_id
  routine_id   = "gdelt_extraction"
  routine_type = "TABLE_VALUED_FUNCTION"
  language     = "SQL"

  definition_body = file("${path.module}/queries/gdelt_extraction.sql")
}

resource "google_bigquery_routine" "daily_correlation" {
  project      = var.project_id
  dataset_id   = google_bigquery_dataset.mvt_analytics.dataset_id
  routine_id   = "daily_correlation"
  routine_type = "QUERY"
  language     = "SQL"

  definition_body = file("${path.module}/queries/daily_correlation.sql")
}

output "dataset_id" {
  value = google_bigquery_dataset.mvt_analytics.dataset_id
}

output "project_id" {
  value = var.project_id
}

output "tables" {
  value = {
    inequality_signals    = google_bigquery_table.inequality_signals.table_id
    sentiment_events      = google_bigquery_table.sentiment_events.table_id
    sovereign_indicators  = google_bigquery_table.sovereign_indicators.table_id
    gdelt_events          = google_bigquery_table.gdelt_events.table_id
  }
}
