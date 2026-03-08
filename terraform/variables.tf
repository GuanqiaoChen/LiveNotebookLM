variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region (e.g., us-central1)"
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Cloud Run service name"
  type        = string
  default     = "live-notebook-lm"
}

variable "artifact_registry_repo" {
  description = "Artifact Registry repository ID"
  type        = string
  default     = "live-notebook-lm"
}

variable "bucket_suffix" {
  description = "Suffix for GCS bucket (full name: project_id-bucket_suffix)"
  type        = string
  default     = "live-notebook-lm-docs"
}

variable "image" {
  description = "Container image URI. Override with -var or TF_VAR_image when deploying."
  type        = string
  default     = "gcr.io/cloudrun/hello"
}

variable "agent_model" {
  description = "Gemini model for Live API (Vertex AI format)"
  type        = string
  default     = "gemini-live-2.5-flash-native-audio"
}

variable "cpu" {
  description = "Cloud Run CPU allocation"
  type        = string
  default     = "1"
}

variable "memory" {
  description = "Cloud Run memory allocation (e.g., 512Mi, 1Gi)"
  type        = string
  default     = "1Gi"
}

variable "allow_unauthenticated" {
  description = "Allow unauthenticated access to Cloud Run"
  type        = bool
  default     = false
}
