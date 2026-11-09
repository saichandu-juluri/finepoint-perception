# docs/SERVER.md: one private bucket and one small server in its own network. Every name starts finepoint-perception.
# Nothing that already exists in the account is read; the only lookups are AWS's public catalogue (image, zones).

locals {
  name = "finepoint-perception"
  tags = { project = local.name }
}

data "aws_ssm_parameter" "ami" {
  count = var.ami_id == "" ? 1 : 0
  name  = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

data "aws_ec2_instance_type_offerings" "zones" {
  location_type = "availability-zone"
  filter {
    name   = "instance-type"
    values = [var.instance_type]
  }
}

# ---- the bucket ----

resource "aws_s3_bucket" "sessions" {
  bucket_prefix = "${local.name}-"
  tags          = { Name = "${local.name}-sessions" }
}

resource "aws_s3_bucket_ownership_controls" "sessions" {
  bucket = aws_s3_bucket.sessions.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "sessions" {
  bucket                  = aws_s3_bucket.sessions.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "sessions" {
  bucket = aws_s3_bucket.sessions.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "sessions" {
  bucket = aws_s3_bucket.sessions.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "sessions" {
  bucket = aws_s3_bucket.sessions.id
  rule {
    id     = "${local.name}-abort-unfinished-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 30
    }
  }
  rule {
    id     = "${local.name}-expire-old-backups"
    status = "Enabled"
    filter {
      prefix = "backups/"
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_s3_bucket_policy" "sessions" {
  bucket = aws_s3_bucket.sessions.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "FinepointPerceptionHttpsOnly"
      Effect    = "Deny"
      Principal = "*"
      Action    = ["s3:*"]
      Resource  = [aws_s3_bucket.sessions.arn, "${aws_s3_bucket.sessions.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.sessions]
}

# the cloud/ folder as the server runs it; new modules are picked up without a change here
data "archive_file" "server" {
  type        = "zip"
  source_dir  = abspath("${path.module}/..")
  output_path = "${path.module}/.build/server.zip"
  excludes = [
    "terraform/**", "**/__pycache__/**", "**/test_*.py", "dev_server.py", "demo.py", "new_lab_key.py",
    "**/.?*", "**/*.db", "**/*.db-*", "**/*.sqlite*", "**/*.log", "**/*.tfvars", "**/config.json",
  ]
}

resource "aws_s3_object" "server" {
  bucket       = aws_s3_bucket.sessions.id
  key          = "deploy/server.zip"
  source       = data.archive_file.server.output_path
  source_hash  = data.archive_file.server.output_base64sha256
  content_type = "application/zip"
  tags         = { Name = "${local.name}-server-zip" }
  depends_on   = [aws_s3_bucket_server_side_encryption_configuration.sessions, aws_s3_bucket_policy.sessions]
}

# ---- the network: all new, all free ----

resource "aws_vpc" "main" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${local.name}-vpc" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.42.1.0/24"
  availability_zone       = sort(data.aws_ec2_instance_type_offerings.zones.locations)[0]
  map_public_ip_on_launch = true
  tags                    = { Name = "${local.name}-public" }
  # a zone AWS adds later must not replace the subnet, and the server with it
  lifecycle {
    ignore_changes = [availability_zone]
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-igw" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "web" {
  name        = "${local.name}-web"
  description = "FinePoint Perception: HTTPS and SSH only"
  vpc_id      = aws_vpc.main.id
  ingress {
    description = "HTTPS, certificates by TLS-ALPN-01"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "SSH, key only"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    description = "All out: S3, Lets Encrypt, package updates"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${local.name}-web" }
}

resource "aws_key_pair" "server" {
  key_name   = local.name
  public_key = file(pathexpand(var.ssh_public_key_file))
  tags       = { Name = local.name }
}

# ---- what the server may do ----

resource "aws_iam_role" "server" {
  name = "${local.name}-server"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "FinepointPerceptionEc2"
      Effect    = "Allow"
      Action    = ["sts:AssumeRole"]
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
  tags = { Name = "${local.name}-server" }
}

resource "aws_iam_instance_profile" "server" {
  name = "${local.name}-server"
  role = aws_iam_role.server.name
  tags = { Name = "${local.name}-server" }
}

resource "aws_iam_role_policy" "s3" {
  name = "${local.name}-s3"
  role = aws_iam_role.server.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "FinepointPerceptionLabObjects"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
        Resource = ["${aws_s3_bucket.sessions.arn}/labs/*"]
      },
      {
        Sid       = "FinepointPerceptionList"
        Effect    = "Allow"
        Action    = ["s3:ListBucket"]
        Resource  = [aws_s3_bucket.sessions.arn]
        Condition = { StringLike = { "s3:prefix" = ["labs/*", "backups/*"] } }
      },
      {
        # s3:prefix is not a condition key of this action, so a prefix condition would always deny
        Sid      = "FinepointPerceptionListUploads"
        Effect   = "Allow"
        Action   = ["s3:ListBucketMultipartUploads"]
        Resource = [aws_s3_bucket.sessions.arn]
      },
      {
        Sid      = "FinepointPerceptionBackup"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = ["${aws_s3_bucket.sessions.arn}/backups/perception.db"]
      },
      {
        Sid      = "FinepointPerceptionDeploy"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${aws_s3_bucket.sessions.arn}/deploy/*"]
      },
    ]
  })
}

# ---- the server ----

resource "aws_instance" "server" {
  ami                         = var.ami_id != "" ? var.ami_id : nonsensitive(data.aws_ssm_parameter.ami[0].value)
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.public.id
  vpc_security_group_ids      = [aws_security_group.web.id]
  iam_instance_profile        = aws_iam_instance_profile.server.name
  key_name                    = aws_key_pair.server.key_name
  associate_public_ip_address = true
  user_data = templatefile("${path.module}/boot.sh.tftpl", {
    bucket = aws_s3_bucket.sessions.bucket
    region = var.region
    domain = var.domain
  })

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  credit_specification {
    cpu_credits = "standard"
  }

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
    encrypted   = true
    tags        = merge(local.tags, { Name = "${local.name}-server" })
  }

  tags = { Name = "${local.name}-server" }

  # a newer image or an edited boot script must never stop or replace the server: its address and database would go
  lifecycle {
    ignore_changes = [ami, user_data]
  }

  # the first boot fetches deploy/server.zip with this role
  depends_on = [aws_iam_role_policy.s3, aws_s3_object.server, aws_route_table_association.public]
}

resource "aws_ec2_tag" "server_nic" {
  for_each    = merge(local.tags, { Name = "${local.name}-server" })
  resource_id = aws_instance.server.primary_network_interface_id
  key         = each.key
  value       = each.value
}
