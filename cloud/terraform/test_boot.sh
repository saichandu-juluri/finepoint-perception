#!/bin/bash
# Runs the real boot script (boot.sh.tftpl, rendered by Terraform) in an amazonlinux:2023 arm64 container, with
# systemctl, the aws CLI and the instance metadata service stubbed. Checks what it installs and writes, and that a
# database copy that cannot be restored still lets the boot finish with the server off. Then starts the real server
# from the real deploy/server.zip with CAMRIG_STORE=fake behind the real Caddy 2.11.9 running the server's own
# Caddyfile (Caddy's local CA in place of Let's Encrypt), and asks it over HTTPS with no server name, as Python does for
# an IP. Nothing reaches the AWS account: Terraform runs with dummy keys and a mocked provider.
#
#     bash cloud/terraform/test_boot.sh
set -euo pipefail

if [ "${1:-}" != inside ]; then
  here=$(cd "$(dirname "$0")" && pwd)
  work=$(mktemp -d)
  trap 'rm -rf "$work"' EXIT
  export AWS_ACCESS_KEY_ID=x AWS_SECRET_ACCESS_KEY=x AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null
  cd "$here"
  terraform test -no-color >"$work/tftest.log" 2>&1 || { cat "$work/tftest.log"; echo "FAIL terraform test"; exit 1; }
  cp .build/server.zip "$work/server.zip"
  render() {
    echo "templatefile(\"boot.sh.tftpl\", {bucket = \"finepoint-perception-test\", region = \"us-east-1\", domain = \"$1\"})" |
      terraform console -var ssh_public_key_file=tests/test_key.pub | sed '1d;$d'
  }
  render "" >"$work/boot_ip.sh"
  render perception.example.org >"$work/boot_domain.sh"
  for f in boot_ip boot_domain; do
    bash -n "$work/$f.sh" && echo "PASS $f.sh is valid bash, $(wc -c <"$work/$f.sh" | tr -d ' ') bytes (user_data limit 16384)"
  done
  cp "$0" "$work/test_boot.sh"
  docker run --rm --platform linux/arm64 -v "$work:/t" amazonlinux:2023 bash /t/test_boot.sh inside
  exit
fi

# ---- inside the container ----
fails=0
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }

dnf install -y -q python3.12 sudo >/dev/null
cat >/usr/local/sbin/systemctl <<'EOF'
#!/bin/bash
echo "systemctl $*" >>/t/systemctl.log
if [ "$*" = "start perception-address.service" ]; then exec /usr/local/bin/perception-address; fi
EOF
cat >/usr/local/sbin/aws <<'EOF'
#!/bin/bash
echo "aws $*" >>/t/aws.log
case "$1 $2" in
  "s3 cp") cp /t/server.zip "${@: -1}" ;;
  "s3api list-objects-v2") cat /t/copies 2>/dev/null || echo 0 ;;
  *) exit 1 ;;
esac
EOF
chmod +x /usr/local/sbin/systemctl /usr/local/sbin/aws
cat >/t/imds.py <<'EOF'
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def reply(self, body):
        self.send_response(200); self.end_headers(); self.wfile.write(body)
    def do_PUT(self):
        self.reply(b"token" if self.path == "/latest/api/token" else b"")
    def do_GET(self):
        ok = self.path == "/latest/meta-data/public-ipv4" and self.headers.get("X-aws-ec2-metadata-token") == "token"
        self.reply(b"203.0.113.10" if ok else b"")
HTTPServer(("127.0.0.1", 9999), H).serve_forever()
EOF
python3.12 /t/imds.py 2>/dev/null &
export IMDS_ENDPOINT=http://127.0.0.1:9999

cat >/t/Caddyfile.ip.expected <<'EOF'
{
	admin off
	auto_https disable_redirects
	default_sni 203.0.113.10
	servers {
		protocols h1 h2
	}
}

https://203.0.113.10 {
	tls {
		issuer acme {
			profile shortlived
			disable_http_challenge
		}
	}
	reverse_proxy 127.0.0.1:8080
}
EOF
cat >/t/Caddyfile.domain.expected <<'EOF'
{
	admin off
	auto_https disable_redirects
	servers {
		protocols h1 h2
	}
}

https://perception.example.org {
	tls {
		issuer acme {
			disable_http_challenge
		}
	}
	reverse_proxy 127.0.0.1:8080
}
EOF

bash /t/boot_ip.sh >/t/boot.out 2>&1 && boot=0 || boot=$?
check "boot script exits 0" '[ $boot = 0 ] || { tail -30 /t/boot.out; false; }'
check "systemd-analyze verify: the units" \
  'systemd-analyze verify /etc/systemd/system/perception*.service /etc/systemd/system/perception-backup.timer /etc/systemd/system/caddy.service >/t/units.log 2>&1 || { cat /t/units.log; false; }'
check "Caddy is v2.11.9" '/usr/local/bin/caddy version | grep -q "^v2.11.9 "'
check "boto3 1.43.126 in the venv" '[ "$(/opt/perception/venv/bin/python -c "import boto3; print(boto3.__version__)")" = 1.43.126 ]'
check "/var/lib/perception is 0700 perception" '[ "$(stat -c "%a %U" /var/lib/perception)" = "700 perception" ]'
check "config.json has the bucket, region and IP address" \
  'python3.12 -c "import json; c = json.load(open(\"/etc/perception/config.json\")); assert c == {\"bucket\": \"finepoint-perception-test\", \"region\": \"us-east-1\", \"public_url\": \"https://203.0.113.10\"}, c"'
check "no copy in the bucket: a new database, nothing restored" \
  '[ -f /var/lib/perception/perception.db ] && grep -q "s3api list-objects-v2 .*--no-paginate" /t/aws.log && ! grep -q restore /t/boot.out'
check "the code came from deploy/server.zip" 'grep -q "s3://finepoint-perception-test/deploy/server.zip" /t/aws.log && [ -f /opt/perception/current/server.py ]'
check "server, hourly copy, Caddy and dnf-automatic started; Systems Manager agent off" \
  'grep -qx "systemctl enable --now perception.service perception-backup.timer" /t/systemctl.log && grep -qx "systemctl enable --now caddy.service dnf-automatic.timer" /t/systemctl.log && grep -qx "systemctl disable --now amazon-ssm-agent.service" /t/systemctl.log'
check "dnf-automatic applies security updates" 'grep -q "^upgrade_type = security" /etc/dnf/automatic.conf && grep -q "^apply_updates = yes" /etc/dnf/automatic.conf && [ -f /usr/lib/systemd/system/dnf-automatic.timer ]'
cp /etc/caddy/Caddyfile /t/Caddyfile.ip
check "IP Caddyfile is the setup proven in the account" 'diff /t/Caddyfile.ip.expected /etc/caddy/Caddyfile'
check "caddy validate: IP form" 'caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/t/validate_ip.log 2>&1 || { cat /t/validate_ip.log; false; }'
sed -i 's/^DOMAIN=.*/DOMAIN=perception.example.org/' /etc/perception/boot.env
/usr/local/bin/perception-address >/dev/null
check "domain Caddyfile and public_url" 'diff /t/Caddyfile.domain.expected /etc/caddy/Caddyfile && grep -q "\"public_url\": \"https://perception.example.org\"" /etc/perception/config.json'
check "caddy validate: domain form" 'caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/t/validate_domain.log 2>&1 || { cat /t/validate_domain.log; false; }'

check "first admin as the README says: sudo perception add-lab / add-user" \
  'sudo perception add-lab lab1 Example >/dev/null && sudo perception add-user --email a@example.org --lab lab1 --admin | grep -q "https://perception.example.org/set-password?t="'
echo 1 >/t/copies
rm -f /var/lib/perception/perception.db /var/lib/perception/perception.db-wal /var/lib/perception/perception.db-shm
: >/t/systemctl.log
bash /t/boot_ip.sh >/t/boot2.out 2>&1 && boot=0 || boot=$?
check "a copy in the bucket that cannot be restored: the boot still finishes" '[ $boot = 0 ] || { tail -20 /t/boot2.out; false; }'
check "... with no new database, and the server and its hourly copy off" \
  '[ ! -f /var/lib/perception/perception.db ] && grep -q "RESTORE FAILED" /t/boot2.out && ! grep -qF "perception.service perception-backup.timer" /t/systemctl.log'
rm -f /t/copies

install -d -o perception -g perception /var/lib/perception/fake
CAMRIG_STORE=fake CAMRIG_CLOUD_DEV_DIR=/var/lib/perception/fake perception serve --listen 127.0.0.1:8080 >/t/server.log 2>&1 &
python3.12 - <<'EOF'
import re
s = open("/t/Caddyfile.ip").read()
s = re.sub(r"\n\ttls \{\n.*?\n\t\}\n", "\n\ttls internal\n", s, flags=re.S)
open("/t/Caddyfile.test", "w").write(s.replace("\tadmin off\n", "\tadmin off\n\tskip_install_trust\n"))
EOF
XDG_DATA_HOME=/t/caddydata caddy run --config /t/Caddyfile.test --adapter caddyfile >/t/caddy.log 2>&1 &
cat >/t/https_get.py <<'EOF'
import socket, ssl, sys
ctx = ssl.create_default_context(cafile="/t/caddydata/caddy/pki/authorities/local/root.crt")
with socket.create_connection(("127.0.0.1", 443), timeout=5) as raw, ctx.wrap_socket(raw, server_hostname="203.0.113.10") as s:
    s.sendall(f"GET {sys.argv[1]} HTTP/1.1\r\nHost: 203.0.113.10\r\nConnection: close\r\n\r\n".encode())
    print(b"".join(iter(lambda: s.recv(65536), b"")).decode(errors="replace"))
EOF
for _ in $(seq 50); do python3.12 /t/https_get.py /health >/dev/null 2>&1 && break; sleep 0.2; done
check "HTTPS to the IP with no server name: /health" 'python3.12 /t/https_get.py /health | grep -q "\"ok\": *true" || { tail -5 /t/caddy.log /t/server.log; false; }'
check "HTTPS to the IP with no server name: /admin/signin answers 200" 'python3.12 /t/https_get.py /admin/signin | head -1 | grep -q "^HTTP/1.1 200"'
check "nothing answers on port 80" '! curl -s -o /dev/null --max-time 3 http://127.0.0.1:80/'
check "the server runs as perception" \
  '[ "$(for p in /proc/[0-9]*; do tr "\0" " " <$p/cmdline 2>/dev/null | grep -q "server.py serve" && stat -c %U $p; done | sort -u)" = perception ]'

echo "$([ $fails = 0 ] && echo PASS || echo FAIL): boot test, $fails failed"
exit $fails
