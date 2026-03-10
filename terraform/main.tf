terraform {
  required_version = ">= 1.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# -----------------------------
# Enable required Google APIs
# -----------------------------
resource "google_project_service" "run" {
  project            = var.project_id
  service            = "run.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "artifact_registry" {
  project            = var.project_id
  service            = "artifactregistry.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "storage" {
  project            = var.project_id
  service            = "storage.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "vertex_ai" {
  project            = var.project_id
  service            = "aiplatform.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "cloud_build" {
  project            = var.project_id
  service            = "cloudbuild.googleapis.com"
  disable_on_destroy = false
}

# -----------------------------
# Artifact Registry
# -----------------------------
resource "google_artifact_registry_repository" "app" {
  project       = var.project_id
  location      = var.region
  repository_id = var.artifact_registry_repo
  format        = "DOCKER"
  description   = "LiveNotebookLM container images"

  depends_on = [
    google_project_service.artifact_registry
  ]
}

# -----------------------------
# Cloud Storage bucket
# -----------------------------
resource "google_storage_bucket" "documents" {
  project  = var.project_id
  name     = "${var.project_id}-${var.bucket_suffix}"
  location = var.region

  uniform_bucket_level_access = true

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [
    google_project_service.storage
  ]
}

# -----------------------------
# Cloud Run service
# -----------------------------
resource "google_cloud_run_v2_service" "app" {
  name     = var.service_name
  location = var.region
  project  = var.project_id

  template {
    service_account = var.service_account_email

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
      }

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }

      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.region
      }

      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "true"
      }

      env {
        name  = "LIVE_NOTEBOOK_AGENT_MODEL"
        value = var.agent_model
      }

      env {
        name  = "GCS_BUCKET"
        value = google_storage_bucket.documents.name
      }

    }
  }

  depends_on = [
    google_project_service.run,
    google_project_service.vertex_ai,
    google_project_service.storage
  ]
}

# -----------------------------
# Public invoker access
# -----------------------------
resource "google_cloud_run_v2_service_iam_member" "public" {
  count = var.allow_unauthenticated ? 1 : 0

  project  = var.project_id
  location = google_cloud_run_v2_service.app.location
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}