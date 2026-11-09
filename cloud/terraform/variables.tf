variable "region" {
  description = "AWS region for everything."
  type        = string
  default     = "us-east-1"
  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]$", var.region))
    error_message = "A region name such as us-east-1."
  }
}

variable "domain" {
  description = "The server's web address, once the owner's DNS points it at the server's public IPv4. Empty: the bare IPv4 with a short-lived Let's Encrypt certificate."
  type        = string
  default     = ""
  validation {
    condition     = var.domain == "" || can(regex("^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,63}$", var.domain))
    error_message = "A lowercase host name such as perception.example.org, or empty."
  }
}

variable "instance_type" {
  description = "The server's size."
  type        = string
  default     = "t4g.small"
}

variable "ami_id" {
  description = "Amazon Linux 2023 arm64 image. Empty: the newest one from AWS's public parameter."
  type        = string
  default     = ""
}

variable "ssh_public_key_file" {
  description = "The public half of the key that logs in as ec2-user over SSH."
  type        = string
  validation {
    # a private key here would be sent to AWS and kept in the plan and the state
    condition     = can(regex("^(ssh-ed25519|ssh-rsa) ", file(pathexpand(var.ssh_public_key_file))))
    error_message = "A public key file (.pub) for SSH, such as ~/.ssh/id_ed25519.pub."
  }
}
