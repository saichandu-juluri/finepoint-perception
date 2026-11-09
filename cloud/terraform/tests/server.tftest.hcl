# Offline: the AWS provider is mocked, nothing reaches AWS. command = apply so ARNs and policies are known.

variables {
  ssh_public_key_file = "tests/test_key.pub"
}

mock_provider "aws" {
  mock_resource "aws_s3_bucket" {
    defaults = {
      arn    = "arn:aws:s3:::finepoint-perception-test"
      bucket = "finepoint-perception-test"
      id     = "finepoint-perception-test"
    }
  }
  mock_resource "aws_instance" {
    defaults = {
      id                           = "i-0123456789abcdef0"
      public_ip                    = "203.0.113.10"
      primary_network_interface_id = "eni-0123456789abcdef0"
    }
  }
  mock_data "aws_ssm_parameter" {
    defaults = { value = "ami-0123456789abcdef0", insecure_value = "ami-0123456789abcdef0" }
  }
  mock_data "aws_ec2_instance_type_offerings" {
    defaults = { locations = ["us-east-1b", "us-east-1a"] }
  }
}

run "server" {
  command = apply

  assert {
    condition     = startswith(aws_s3_bucket.sessions.bucket_prefix, "finepoint-perception-")
    error_message = "the bucket name must start finepoint-perception-"
  }
  assert {
    condition = alltrue([for n in [
      aws_security_group.web.name, aws_iam_role.server.name, aws_iam_instance_profile.server.name, aws_iam_role_policy.s3.name,
      aws_key_pair.server.key_name, aws_instance.server.key_name,
      aws_s3_bucket.sessions.tags.Name, aws_s3_object.server.tags.Name, aws_vpc.main.tags.Name,
      aws_subnet.public.tags.Name, aws_internet_gateway.main.tags.Name, aws_route_table.public.tags.Name,
      aws_security_group.web.tags.Name, aws_iam_role.server.tags.Name, aws_iam_instance_profile.server.tags.Name,
      aws_key_pair.server.tags.Name, aws_instance.server.tags.Name, aws_instance.server.root_block_device[0].tags.Name,
      aws_ec2_tag.server_nic["Name"].value,
    ] : startswith(n, "finepoint-perception")])
    error_message = "every name and Name tag must start finepoint-perception"
  }
  assert {
    condition     = alltrue([for r in aws_s3_bucket_lifecycle_configuration.sessions.rule : startswith(r.id, "finepoint-perception-")])
    error_message = "every lifecycle rule id must start finepoint-perception-"
  }
  assert {
    condition = alltrue([for s in flatten([for p in [
      aws_iam_role_policy.s3.policy, aws_s3_bucket_policy.sessions.policy, aws_iam_role.server.assume_role_policy,
    ] : jsondecode(p).Statement]) : startswith(s.Sid, "FinepointPerception")])
    error_message = "every policy statement Sid must start FinepointPerception"
  }
  assert {
    condition     = jsondecode(aws_s3_bucket_policy.sessions.policy).Statement[0].Sid == "FinepointPerceptionHttpsOnly"
    error_message = "the bucket policy's Sid must be FinepointPerceptionHttpsOnly"
  }
  assert {
    condition     = aws_instance.server.root_block_device[0].tags.project == "finepoint-perception" && aws_ec2_tag.server_nic["project"].value == "finepoint-perception"
    error_message = "the volume and the network interface must carry project = finepoint-perception"
  }
  assert {
    condition = (aws_instance.server.instance_type == "t4g.small" && aws_instance.server.associate_public_ip_address
      && aws_instance.server.metadata_options[0].http_tokens == "required" && aws_instance.server.metadata_options[0].http_put_response_hop_limit == 1
    && aws_instance.server.credit_specification[0].cpu_credits == "standard")
    error_message = "t4g.small, public IPv4, IMDSv2 only with hop limit 1, standard CPU credits"
  }
  assert {
    condition = (aws_instance.server.root_block_device[0].volume_size == 20 && aws_instance.server.root_block_device[0].volume_type == "gp3"
    && aws_instance.server.root_block_device[0].encrypted)
    error_message = "the root disk must be 20 GB gp3, encrypted"
  }
  assert {
    condition     = aws_instance.server.ami == "ami-0123456789abcdef0" && aws_subnet.public.availability_zone == "us-east-1a"
    error_message = "the image comes from the public parameter and the zone is the first that offers the type"
  }
  assert {
    condition = (aws_key_pair.server.key_name == "finepoint-perception" && aws_instance.server.key_name == "finepoint-perception"
    && aws_key_pair.server.public_key == file("tests/test_key.pub"))
    error_message = "the server logs in with key pair finepoint-perception, made from ssh_public_key_file"
  }
  assert {
    condition = (length(aws_security_group.web.ingress) == 2
      && toset([for r in aws_security_group.web.ingress : r.from_port]) == toset([22, 443])
      && alltrue([for r in aws_security_group.web.ingress : r.to_port == r.from_port && r.protocol == "tcp" && r.self != true
        && join(",", r.cidr_blocks) == "0.0.0.0/0" && length(r.ipv6_cidr_blocks == null ? [] : r.ipv6_cidr_blocks) == 0
    && length(r.security_groups == null ? [] : r.security_groups) == 0 && length(r.prefix_list_ids == null ? [] : r.prefix_list_ids) == 0]))
    error_message = "the security group must open only TCP 22 and 443 from 0.0.0.0/0"
  }
  assert {
    condition = alltrue([for s in flatten([for p in [aws_iam_role_policy.s3.policy, aws_s3_bucket_policy.sessions.policy] :
    jsondecode(p).Statement]) : alltrue([for a in s.Action : !strcontains(lower(a), "delete")])])
    error_message = "no policy action may contain Delete"
  }
  assert {
    condition = !contains(flatten([for p in [aws_iam_role_policy.s3.policy, aws_s3_bucket_policy.sessions.policy] :
    [for s in jsondecode(p).Statement : s.Resource]]), "*")
    error_message = "no policy may have Resource \"*\""
  }
  assert {
    condition = length(setsubtract(
      toset(flatten([for s in jsondecode(aws_iam_role_policy.s3.policy).Statement : s.Resource])),
      toset([
        "arn:aws:s3:::finepoint-perception-test", "arn:aws:s3:::finepoint-perception-test/labs/*",
        "arn:aws:s3:::finepoint-perception-test/backups/perception.db", "arn:aws:s3:::finepoint-perception-test/deploy/*",
      ]),
    )) == 0
    error_message = "the role may name only the bucket and its labs/, backups/perception.db and deploy/ keys"
  }
  assert {
    condition = toset(flatten([for s in jsondecode(aws_s3_bucket_policy.sessions.policy).Statement : s.Resource])) == toset([
    "arn:aws:s3:::finepoint-perception-test", "arn:aws:s3:::finepoint-perception-test/*"])
    error_message = "the bucket policy names only the bucket"
  }
  assert {
    condition     = jsondecode(aws_iam_role.server.assume_role_policy).Statement[0].Principal.Service == "ec2.amazonaws.com"
    error_message = "the role is for EC2 only"
  }
  assert {
    condition     = aws_s3_object.server.key == "deploy/server.zip"
    error_message = "the code goes to deploy/server.zip"
  }
  assert {
    condition     = strcontains(aws_instance.server.user_data, "BUCKET=finepoint-perception-test") && strcontains(aws_instance.server.user_data, "DOMAIN=\n")
    error_message = "the boot script gets the bucket, and no domain by default"
  }
  assert {
    condition     = output.url == "https://203.0.113.10" && output.bucket == "finepoint-perception-test" && output.instance_id == "i-0123456789abcdef0"
    error_message = "with no domain the address is the public IPv4"
  }
}

run "later_edits_keep_the_server" {
  command = apply
  variables {
    domain = "perception.example.org"
    ami_id = "ami-0fedcba9876543210"
  }
  assert {
    condition     = output.url == "https://perception.example.org"
    error_message = "with a domain the address is the domain"
  }
  assert {
    condition     = aws_instance.server.ami == "ami-0123456789abcdef0" && strcontains(aws_instance.server.user_data, "DOMAIN=\n")
    error_message = "a new image or boot script must leave the running server alone"
  }
}

run "fixed_image" {
  command   = apply
  state_key = "fixed_image"
  variables {
    domain = "perception.example.org"
    ami_id = "ami-0fedcba9876543210"
  }
  assert {
    condition     = length(data.aws_ssm_parameter.ami) == 0 && aws_instance.server.ami == "ami-0fedcba9876543210"
    error_message = "ami_id skips the public parameter"
  }
  assert {
    condition     = strcontains(aws_instance.server.user_data, "DOMAIN=perception.example.org\n")
    error_message = "a new server's boot script gets the domain"
  }
}

run "bad_domain" {
  command = plan
  variables {
    domain = "https://Perception.example.org"
  }
  expect_failures = [var.domain]
}

run "not_a_public_key" {
  command = plan
  variables {
    ssh_public_key_file = "tests/server.tftest.hcl"
  }
  expect_failures = [var.ssh_public_key_file]
}
