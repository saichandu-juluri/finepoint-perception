# FinePoint Perception in AWS

The server and its bucket, as Terraform. Run the commands at the end only with the owner's approval: they change a real
AWS account. For building and testing, the local stand-in (`pixi run --frozen -e cloud cloud-dev`) plays the cloud.
The shapes are in `docs/SERVER.md` and `docs/UPLOAD.md`.

## What it creates

21 resources, all new. Every name starts `finepoint-perception`; every taggable resource carries
`project = finepoint-perception` and a `Name` starting `finepoint-perception`. Nothing that already exists in the
account is read or changed: the only lookups are AWS's public catalogue (the newest Amazon Linux 2023 arm64 image from
the public parameter `/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64`, and a zone that offers
t4g.small).

| Resource | Name | What it is |
| --- | --- | --- |
| `aws_s3_bucket.sessions` | `finepoint-perception-<random suffix>` | The one bucket: every lab's sessions under `labs/<lab>/`, the hourly database copy at `backups/perception.db`, the server's code at `deploy/server.zip`. |
| bucket ownership, public access block, versioning, encryption | | ACLs off, all four public-access blocks on, versioning on, SSE-S3 (AES256). |
| `aws_s3_bucket_lifecycle_configuration` | rules `finepoint-perception-abort-unfinished-uploads`, `finepoint-perception-expire-old-backups` | Unfinished uploads removed after 30 days; old copies of the database under `backups/` removed 30 days after a newer one replaced them. |
| `aws_s3_bucket_policy` | Sid `FinepointPerceptionHttpsOnly` | Refuses every request that is not HTTPS. Allows nothing. |
| `aws_s3_object.server` | `deploy/server.zip` | The `cloud/` folder (server, its modules, `requirements.txt`), without tests, `terraform/`, `dev_server.py`, `demo.py` and local files. Made on the owner's computer at plan time (`.build/server.zip`). |
| `aws_vpc`, `aws_subnet`, `aws_internet_gateway`, `aws_route_table` + association | `finepoint-perception-vpc`, `-public`, `-igw`, `-public` | The server's own network: one public subnet, a route to the internet. Free. |
| `aws_security_group.web` | `finepoint-perception-web` | In: TCP 443 (HTTPS) and TCP 22 (SSH, key only) from anywhere, nothing else. Port 80 stays closed. Out: everything. |
| `aws_key_pair.server` | `finepoint-perception` | The public key in the file that the variable `ssh_public_key_file` names, for SSH as `ec2-user`. A private key there is refused before it reaches a plan. |
| `aws_iam_role.server` + `aws_iam_instance_profile.server` | `finepoint-perception-server` | The server's identity, trusted by EC2 only. No AWS managed policies. |
| `aws_iam_role_policy.s3` | `finepoint-perception-s3` | The role's only policy. `labs/*`: PutObject, GetObject, AbortMultipartUpload, ListMultipartUploadParts. ListBucket only for prefixes `labs/*` and `backups/*`. ListBucketMultipartUploads on the bucket. `backups/perception.db`: GetObject, PutObject. `deploy/*`: GetObject. No delete, no `"*"`. |
| `aws_instance.server` | `finepoint-perception-server` | t4g.small, Amazon Linux 2023 arm64, key pair `finepoint-perception`, public IPv4, 20 GB gp3 encrypted, IMDSv2 only (hop limit 1), CPU credits `standard` (it can never bill for bursting). A newer image or an edited boot script never stops or replaces it (`ignore_changes`). |
| `aws_ec2_tag.server_nic` | | The Name and project tags on the server's network interface. |

The first boot (`boot.sh.tftpl`, log in `/var/log/perception-boot.log`) installs Python 3.12 and boto3 (exact
versions and hashes in `cloud/requirements.txt`), Caddy 2.11.9 (checked against its release's SHA-512) and the server
from `deploy/server.zip`, restores the database copy if there is one, and starts:

- `perception`: `server.py serve` on 127.0.0.1:8080, as user `perception`, writing only `/var/lib/perception`.
- `perception-backup.timer`: the database to `backups/perception.db` every hour.
- `perception-address`: on every boot, reads the public IPv4 and writes `/etc/perception/config.json` and the Caddyfile.
- `caddy`: HTTPS on 443 only (HTTP/1.1 and HTTP/2). Certificates come from Let's Encrypt by TLS-ALPN-01 on 443, so
  nothing listens on 80. No access log, admin API off. Certificates stay in `/var/lib/caddy`, so a restart does not
  order new ones.
- `dnf-automatic`: security updates every day.

The Systems Manager agent that the image ships is turned off: login is SSH. If the bucket holds a database copy that
cannot be restored, the boot still finishes but leaves the server and its hourly copy off, so an empty database never
replaces the copy; the boot log says so and gives the commands to finish by hand.

**The address.** With no `domain` the address is `https://<public IPv4>`. Let's Encrypt issues IP-address
certificates only under its 6-day `shortlived` profile, which Caddy renews by itself. A client that connects to an IP
sends no server name (Python's ssl never does), so Caddy's `default_sni` answers it with the IP's certificate. With
`domain` set, point the domain's DNS A record at the IPv4; the address is `https://<domain>`. There is no Elastic IP
(the owner's choice): **reboot, never stop.** A stop and start gives the server a new IPv4; a reboot keeps it. After a
stop and start, read the new one with `terraform output public_ip`, and update the DNS record or tell the labs.

## What it does not create

No Elastic IP, no Systems Manager access, no load balancer, no CloudWatch logs or alarms, no KMS key, no budget (an
AWS Budgets alert set by hand is advised), no Terraform state bucket: state stays in `terraform.tfstate` on the owner's
computer. Keep it safe and out of git (`.gitignore` already skips it, `*.tfvars`, `.build/` and the plan files).

## Monthly cost

US East (N. Virginia), on demand: t4g.small $12.26, public IPv4 $3.65 (AWS charges the same with or without an Elastic
IP), 20 GB gp3 $1.60: **about $18 a month**. Storage is extra: **about $3.70 a month per 160 GB session kept**
($0.023 per GB-month). Uploads into AWS are free.

## Logging in, the first admin, updating

- **Log in**: `ssh -i <private key> ec2-user@<public_ip>` (`terraform output public_ip`). The private key is the
  other half of the key in `ssh_public_key_file`, which has no default: name your public key in
  `terraform.tfvars` (git skips it), for example `ssh_public_key_file = "~/.ssh/id_ed25519.pub"`. Key only.
- **The first admin**, over SSH:

  ```sh
  sudo perception add-lab lab1 "Example Lab"
  sudo perception add-user --email you@example.org --lab lab1 --name "Your Name" --admin   # prints a set-password link
  ```

  Open the link, set a password, then sign in at `<url>/admin`. More people and labs from the admin page.
- **Update the code**: on the owner's computer `terraform apply` (it uploads a new `deploy/server.zip` and changes
  nothing else), then over SSH `sudo perception-update`. It unpacks beside the running copy, switches over, installs
  the requirements, migrates and restarts. The previous three copies stay in `/opt/perception/releases/`.
- **Restart**: `sudo reboot`. Never stop the instance: that changes its address.
- **Boot problems**: `sudo cat /var/log/perception-boot.log`, `sudo journalctl -u perception -u caddy`.
- A new key in `ssh_public_key_file` replaces the key pair in AWS but not the key on the running server: add it to
  `/home/ec2-user/.ssh/authorized_keys` there too.

## Checks (offline, nothing reaches the AWS account)

```sh
cd cloud/terraform
export AWS_ACCESS_KEY_ID=x AWS_SECRET_ACCESS_KEY=x AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null
terraform fmt -check -recursive && terraform init -backend=false && terraform validate
terraform test          # mocked AWS: names, tags, instance settings, ports, every policy statement
bash test_boot.sh       # the boot script in an amazonlinux:2023 container, the server behind the real Caddy
```

## Commands, only after the owner approves

```sh
cd cloud/terraform
terraform init
terraform plan -out=perception.plan                       # optional: -var domain=perception.example.org
terraform show -json perception.plan > plan.json
python3 check_plan.py plan.json                           # must print PASS: 21 resources to create
terraform apply perception.plan
terraform output url                                      # the address for the app's sign-in form
ssh -i PRIVATE_KEY ec2-user@$(terraform output -raw public_ip)    # the other half of ssh_public_key_file
```

Then make the first admin as above. `check_plan.py` fails unless every change is a create of an expected,
finepoint-perception-named, project-tagged resource, the security group lets in only 22 and 443, the role's only
policy is `finepoint-perception-s3`, with no Elastic IP, no managed policy and no lookup of anything existing. The
role policy names the bucket, whose name is known only after apply, so `terraform test` checks its statements.
