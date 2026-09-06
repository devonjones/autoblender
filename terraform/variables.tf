variable "environment" {
  type = string
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be staging or production."
  }
}

variable "infra_state_bucket" {
  description = "S3 bucket holding the openforge-infra state"
  type        = string
}

variable "image_tag" {
  description = "Tag of the autoblender lambda image in ECR (git sha)"
  type        = string
}

variable "drive_root_folder_id" {
  description = "Google Drive folder id of the shared stl-automation root; <process>/in and /out live under it"
  type        = string
}

variable "poll_interval_minutes" {
  type    = number
  default = 1
}

variable "memory_mb" {
  type    = number
  default = 4096
}
