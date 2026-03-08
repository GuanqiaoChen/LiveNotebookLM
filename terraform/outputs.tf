output "cloud_run_url" {
  description = "URL of the deployed Cloud Run service"
  value       = google_cloud_run_v2_service.app.uri
}

output "gcs_bucket" {
  description = "GCS bucket for documents"
  value       = google_storage_bucket.documents.name
}

output "artifact_registry" {
  description = "Artifact Registry repository path"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app.repository_id}"
}
