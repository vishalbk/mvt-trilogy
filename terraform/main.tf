terraform {
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  backend "local" {
    path = "terraform.tfstate"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

module "pubsub" {
  source      = "./modules/pubsub"
  project_id  = var.project_id
  environment = var.environment
}

module "bigquery" {
  source      = "./modules/bigquery"
  project_id  = var.project_id
  environment = var.environment
}

module "firestore" {
  source                = "./modules/firestore"
  project_id            = var.project_id
  environment           = var.environment
  skip_app_engine_init  = var.skip_app_engine_init
}

module "functions" {
  source                  = "./modules/functions"
  project_id              = var.project_id
  region                  = var.region
  environment             = var.environment
  pubsub_signals_topic    = module.pubsub.signals_topic_name
  pubsub_analytics_topic  = module.pubsub.analytics_topic_name
  bigquery_dataset_id     = module.bigquery.dataset_id
  firestore_database      = module.firestore.database_name
  aws_event_relay_endpoint = var.aws_event_relay_endpoint
}

module "hosting" {
  source      = "./modules/hosting"
  project_id  = var.project_id
  environment = var.environment
}

module "monitoring" {
  source      = "./modules/monitoring"
  project_id  = var.project_id
  environment = var.environment
  region      = var.region
}
