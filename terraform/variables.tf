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

variable "memory_mb" {
  type    = number
  default = 4096
}

variable "api_concurrency" {
  description = "Reserved concurrency of the API Lambda: the most jobs that run at once"
  type        = number
  default     = 4
}
