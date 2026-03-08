# LiveNotebookLM - Infrastructure as Code
# Cloud Run + Cloud Storage + Artifact Registry

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

# --- APIs ---
resource "google_project_service" "run" {
  service            = "run.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "artifact_registry" {
  service            = "artifactregistry.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "storage" {
  service            = "storage.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "vertex_ai" {
  service            = "aiplatform.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "cloud_build" {
  service            = "cloudbuild.googleapis.com"
  disable_on_destroy = false
}

# --- Artifact Registry (for Docker images) ---
resource "google_artifact_registry_repository" "app" {
  location      = var.region
  repository_id = var.artifact_registry_repo
  format        = "DOCKER"
  description   = "LiveNotebookLM container images"

  depends_on = [google_project_service.artifact_registry]
}

# --- Cloud Storage (for documents/sources, NotebookLM-style) ---
resource "google_storage_bucket" "documents" {
  name     = "${var.project_id}-${var.bucket_suffix}"
  location = var.region

  uniform_bucket_level_access = true

  depends_on = [google_project_service.storage]
}

# --- Cloud Run Service ---
# Image is built and pushed by deploy.sh; pass via TF_VAR_image or -var
resource "google_cloud_run_v2_service" "app" {
  name     = var.service_name
  location = var.region

  template {
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
        name  = "GCS_BUCKET_NAME"
        value = google_storage_bucket.documents.name
      }
    }
  }

  depends_on = [
    google_project_service.run,
    google_project_service.vertex_ai,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  count = var.allow_unauthenticated ? 1 : 0

  location = google_cloud_run_v2_service.app.location
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
