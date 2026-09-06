terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Bucket and key are passed at init time so one config targets any environment:
  #   tofu init -backend-config="bucket=openforge-infra-tfstate-<account>" \
  #             -backend-config="key=autoblender/<env>/terraform.tfstate"
  backend "s3" {
    region         = "us-east-1"
    dynamodb_table = "openforge-infra-tfstate-lock"
    encrypt        = true
  }
}

provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = "autoblender"
      Environment = var.environment
      ManagedBy   = "opentofu"
      Repo        = "devonjones/autoblender"
    }
  }
}
