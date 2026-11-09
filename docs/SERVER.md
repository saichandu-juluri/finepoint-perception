# FinePoint Perception server: the contract

One small server (EC2 t4g.small) hands out the upload links of `docs/UPLOAD.md`, keeps the users list and a record of
every upload, and serves a small admin page. Change the shapes here first, then in code.

## What runs where

- The server: `cloud/server.py`, a Python HTTP server on `127.0.0.1:8080` behind Caddy. Caddy serves HTTPS on 443, and
  port 80 stays closed (the certificate is proven over TLS-ALPN on 443). Python 3.12 or newer: the standard library and
  boto3, nothing else. It serves the upload calls of `docs/UPLOAD.md`, the sign-in calls and the admin pages below. It
  logs only the method, the path without its query string, and the status: never a body, a header or a token. Caddy
  keeps no access log.
- The database: one SQLite file, `/var/lib/perception/perception.db`, WAL mode, foreign keys on, a busy timeout of 5 s.
  Copied to the bucket every hour.
- Video bytes never pass through the server: the lab computer sends every piece straight to S3 with a signed link.
- The lab computer keeps its sign-in token in the OS password safe (macOS Keychain, Windows Credential Manager) through
  Python's `keyring`: service `FinePoint Perception`, account = the server address. Never in a file, a URL, the browser,
  a command line or a log.

## The database (schema version 1)

```sql
CREATE TABLE schema_version (version INTEGER NOT NULL);
CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);   -- csrf_secret: 32 random bytes, hex, made by migrate
CREATE TABLE labs (
  name TEXT PRIMARY KEY,              -- [a-z0-9-]{1,40}; the <lab> of every S3 key
  title TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT NOT NULL DEFAULT '',
  lab TEXT NOT NULL REFERENCES labs(name),
  admin INTEGER NOT NULL DEFAULT 0 CHECK (admin IN (0, 1)),
  password TEXT,                      -- NULL until the person sets it
  disabled_at TEXT,
  created_at TEXT NOT NULL);
CREATE TABLE tokens (
  hash TEXT PRIMARY KEY,              -- SHA-256 hex of the token
  user_id INTEGER NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL CHECK (kind IN ('app', 'admin', 'setup')),
  label TEXT NOT NULL DEFAULT '',     -- app: the computer's name
  created_at TEXT NOT NULL,
  expires_at TEXT,
  last_used_at TEXT,
  revoked_at TEXT);
CREATE TABLE uploads (
  id INTEGER PRIMARY KEY,
  lab TEXT NOT NULL,
  session TEXT NOT NULL,
  path TEXT NOT NULL,                 -- the key below labs/<lab>/sessions/<session>/
  kind TEXT NOT NULL,
  size INTEGER NOT NULL,
  upload_id TEXT UNIQUE,              -- NULL for already_there
  user_id INTEGER REFERENCES users(id),
  computer TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL CHECK (status IN ('uploading', 'done', 'aborted', 'already_there')),
  started_at TEXT NOT NULL,
  finished_at TEXT);
```

- Times are UTC, ISO 8601 to the second: `2026-11-09T14:02:11Z`.
- Password: `scrypt$15$8$3$<salt>$<hash>` (n = 2^15, r = 8, p = 3: 32 MiB, one of OWASP's equal-strength scrypt
  settings; a 16-byte random salt, a 32-byte hash, both base64), compared with `hmac.compare_digest`. At least 12
  characters, at most 256. The rate limit is checked before hashing, and at most two password checks run at a time.
- Token: `fpp_` + `secrets.token_urlsafe(32)`. Only its SHA-256 is stored.
  - `app`: one computer's sign-in. No expiry; it ends when the person signs out or an admin signs the computer out.
  - `admin`: the admin page's cookie, 12 hours.
  - `setup`: a set-password link, 72 hours, used once.
- Nobody is ever deleted: disabling a person keeps their upload history and revokes all their tokens.
- `uploads` gets one row per `/uploads/start`: `uploading`, or `already_there` when the start answers `done`.
  `/uploads/complete` sets `done`, `/uploads/abort` sets `aborted`, both with `finished_at`. A session is complete when
  its newest `manifest.json` row is `done` and none of its rows is `uploading`.

## Calls

Every call of `docs/UPLOAD.md` is unchanged except the key: `Authorization: Bearer <app token>`, and the lab is the
person's lab. A token that is unknown, revoked or expired, or whose person is disabled, gets
401 `{"error": "signed out: sign in again"}`. Request bodies are JSON, at most 2 MiB (a 10,000-part complete is about
1.2 MB).

| Call | Body | Answer |
| --- | --- | --- |
| `POST /auth/signin` | `{email, password, computer}` | `{token, email, name, lab}`; 401 `wrong email or password` for every failure (unknown email, no password yet, disabled, wrong password); 429 after 10 failed tries for one email, or 30 from one address, within 15 minutes |
| `POST /auth/signout` | | `{}`; revokes the token it carries |
| `GET /auth/me` | | `{email, name, lab}` |
| `GET /health` | | `{ok: true}`, plus `lab` when a valid token comes with it |

The failure counts live in memory (one process); the client address is `X-Forwarded-For` only when the request comes
from Caddy on 127.0.0.1.

## Admin pages

Server-rendered HTML, no JavaScript, one stylesheet at `/admin/style.css`. Admins only (`users.admin = 1`).

- `GET|POST /admin/signin`: the same sign-in rules and limits. Success sets cookie `fpp_admin` (`HttpOnly`,
  `SameSite=Strict`, `Path=/`, `Secure` when the request came over HTTPS, 12 hours) and goes to `/admin`.
- `GET /admin`: one page, four tables.
  - Uploads by session: lab, session, files done of files started, size, last activity, who, complete or in progress.
  - People: email, name, lab, admin, state (`invited`, `active`, `disabled`), last sign-in.
  - Labs: name, title, people, sessions.
  - Computers: person, computer, signed in, last used.
- Forms (POST, each with a CSRF field = HMAC-SHA256 of the admin token, keyed with `settings.csrf_secret`; the `Origin`
  header must be the server's own):
  add a lab; add a person (email, name, lab, admin), which shows that person's set-password link once; a new link for a
  person; disable or enable a person; sign a computer out; sign out.
- `GET|POST /set-password?t=<setup token>`: the new password twice. Using the link revokes it.
- Every page sends `Content-Security-Policy: default-src 'none'; style-src 'self'; form-action 'self';
  frame-ancestors 'none'; base-uri 'none'`, `Referrer-Policy: same-origin` (with `no-referrer` browsers send
  `Origin: null` on a form post, so every form would be refused), `X-Content-Type-Options: nosniff`,
  `Cache-Control: no-store`. Every value on a page is HTML-escaped.
- The admin sends the set-password link to the person by their own email. The server sends no email.

## Server commands

`python server.py <command>`, run on the server over SSH as `sudo perception <command>`:

- `serve [--listen 127.0.0.1:8080]`
- `migrate`: creates or upgrades the schema; safe to run again.
- `add-lab <name> <title>`
- `add-user --email E --lab L [--name N] [--admin]`: prints the set-password link.
- `link --email E`: prints a new set-password link.
- `backup`: copies the database with SQLite's backup API to `s3://<bucket>/backups/perception.db`.
- `restore`: only when no database file exists; fetches that copy.

Settings: `/etc/perception/config.json` `{bucket, region, public_url}` (`PERCEPTION_CONFIG` names another file).
`CAMRIG_STORE=fake` selects the local stand-in for S3.

## The lab computer

- `python -m camrig.cloud_account <command>`:
  - `signin`: reads `{url, email, password}` as JSON on stdin; keeps the token in the password safe and
    `{url, email, name, lab}` in `~/.camrig/cloud.json`; prints `{email, name, lab}`.
  - `signout`: revokes the token on the server and removes it from the password safe.
  - `status`: prints `{url, signedIn, email, name, lab, fromEnv}`.
  - `files <session>`: prints the answer of `GET /sessions/<session>/files`.
- The cloud for the uploader and the app: address from `CAMRIG_CLOUD_URL`, else `~/.camrig/cloud.json` `url`; token
  from `CAMRIG_CLOUD_KEY`, else the password safe.
- No token at all: the uploader exits with progress.json `waiting_for_cloud`, "Sign in to upload", so the app's
  automatic starts never mark a signed-out computer's sessions failed. A 401: `failed`, "Signed out of the cloud: sign in
  again in the app". No retry.
- The app's Upload panel shows a sign-in form (server address, email, password) until signed in, then
  "Signed in as <email> (lab <lab>)" and Sign out. Start needs a sign-in. The app's server runs the commands above; the
  password goes over stdin and is never kept; the token never reaches the browser. `CAMRIG_READONLY` refuses sign-in
  and sign-out. With the token from `CAMRIG_CLOUD_KEY` (the demo, the tests) the panel says so and has no Sign out.
- Tests never touch the real password safe: Python tests set `PYTHON_KEYRING_BACKEND` to a file backend
  (`camrig.keyring_file`) in a temporary folder; one macOS check uses a temporary keychain file.

## The local stand-in

`cloud/dev_server.py` keeps its command line and its ready line, `ready http://127.0.0.1:<port> lab dev key
perception-dev-key`. It runs `server.py` with the fake store and a database in its data folder, seeded on every start
with lab `dev`, person `lab@example.com` (password `lab-dev-password`, app token `perception-dev-key`) and admin
`admin@example.com` (password `admin-dev-password`). The admin page is at `<address>/admin`.

## AWS (Terraform in `cloud/terraform/`)

Every name starts with `finepoint-perception` and every resource carries the tag `project = finepoint-perception`.
Nothing outside these resources is created, read or changed, and no policy names anything else.

| Resource | Name | What it is |
| --- | --- | --- |
| S3 bucket | `finepoint-perception-` + a suffix AWS makes unique (`bucket_prefix`) | private, versioned, encrypted, HTTPS only, unfinished uploads stopped after 30 days; old copies under `backups/` expire after 30 days |
| S3 object | `deploy/server.zip` | the server's code and `requirements.txt` (boto3 pinned by hash); the instance installs it |
| VPC, subnet, internet gateway, route table | `finepoint-perception` | the server's own network, so nothing already in the account is read or shared; free |
| EC2 instance | `finepoint-perception-server` | t4g.small, Amazon Linux 2023 (arm64), 20 GB encrypted gp3, IMDSv2 only, CPU credits `standard`, a public IPv4 that AWS assigns |
| Security group | `finepoint-perception-web` | in: 443 and 22 (SSH, key only) from anywhere; nothing else, port 80 closed |
| Key pair | `finepoint-perception` | your SSH public key (variable `ssh_public_key_file`) |
| IAM role + instance profile | `finepoint-perception-server` | inline `finepoint-perception-s3`: `labs/*`: PutObject, GetObject, AbortMultipartUpload, ListMultipartUploadParts; ListBucket under `labs/` and `backups/`; ListBucketMultipartUploads; `backups/perception.db`: GetObject, PutObject; `deploy/*`: GetObject. No AWS managed policy, no `*` resource, no delete anywhere |

- No Elastic IP. The public IPv4 stays through reboots but changes when the server is stopped and started or replaced;
  lab computers then sign in again at the new address. AWS bills an assigned public IPv4 the same $3.65 a month as an
  Elastic IP, so leaving it out saves nothing.
- Address: `https://<var.domain>` when your DNS points the domain at the server's IP. With no domain, the address is the
  IP itself: Let's Encrypt has issued IP-address certificates since January 2026, only under its `shortlived` profile
  (about 6 days), and Caddy 2.11 renews them on its own over TLS-ALPN on 443 (port 80 stays closed; `default_sni` is
  the IP because a client connecting to an IP sends no server name). sslip.io-style names are not used: they share one
  Let's Encrypt rate limit with every other user and often hit it.
- Every boot, before Caddy starts, the instance reads its public IPv4 from IMDSv2 and writes `config.json`
  (`{bucket, region, public_url}`) and the Caddyfile, so a new address is picked up on its own.
- Code updates: `terraform apply` uploads a new `deploy/server.zip`, then `sudo perception-update` over SSH as
  `ec2-user` installs it and restarts the server. Reboot, never stop: a stop changes the IP. The instance ignores
  changes to its AMI and boot script, so it is never stopped or replaced for a change; its database survives, and a
  replaced instance restores the last hourly copy.
- The first admin, after the first apply, over SSH: `sudo perception add-lab <lab> <title>`, then
  `sudo perception add-user --email <email> --lab <lab> --admin`, which prints the set-password link.
- Logs stay on the instance (`journalctl -u perception`); no CloudWatch.
- Signed links come from the role's temporary credentials, so a link can stop working before its hour is up when they
  rotate; the uploader already asks for a new link on 403.
- Cost, US East, on demand: t4g.small $12.26, public IPv4 $3.65, 20 GB gp3 $1.60, about $18 a month; storage about
  $3.70 a month for each 160 GB session kept.
