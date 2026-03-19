variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "GCP zone"
  type        = string
  default     = "us-central1-a"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "aws_event_relay_endpoint" {
  description = "AWS EventBridge endpoint URL for cross-cloud event relay"
  type        = string
  sensitive   = true
}

variable "skip_app_engine_init" {
  description = "Skip App Engine initialization (set to true if already exists)"
  type        = bool
  default     = true
}
