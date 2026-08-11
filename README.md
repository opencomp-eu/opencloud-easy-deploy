# OpenCloud Easy Deploy

Production-oriented [OpenCloud](https://opencloud.eu) deployment for VPS hosts. One `deploy.yaml` describes your stack; `apply.sh` renders configs, manages secrets, and starts containers with **Caddy** (TLS) and upstream **[opencloud-compose](https://github.com/opencloud-eu/opencloud-compose)** (unchanged).

This replaces the official test installer (`curl -L https://opencloud.eu/install | bash`), which runs an insecure localhost binary and prints *"This is a fragile test setup, not suitable for production!"*

## Quick start

**Requirements:** Linux VPS, Docker Compose v2, DNS pointing at the server, ports 80/443 open.

```bash
git clone --recurse-submodules https://github.com/your-org/opencloud-easy-deploy.git
cd opencloud-easy-deploy
bash ensure-dependencies.sh   # Docker, uv, submodules, Python deps
bash wizard.sh                # interactive: writes deploy.yaml and deploys
```

If you already cloned without submodules:

```bash
git submodule update --init --recursive
```

Or manually:

```bash
cp deploy.yaml.example deploy.yaml
# edit deploy.yaml — set your domain and paths
bash ensure-dependencies.sh
bash apply.sh
```

## Workflow

| File / script | Role |
|---------------|------|
| `deploy.yaml` | Operator-owned desired state (commit this) |
| `.opencloud-easy-deploy/secrets.yaml` | Generated passwords (gitignored) |
| `opencloud-compose/.env` | Generated Docker Compose env (gitignored) |
| `bash ensure-dependencies.sh` | Install Docker, uv, submodule, Python deps |
| `bash apply.sh` | Converge config and start/restart services |
| `bash wizard.sh` | Interactive `deploy.yaml` creator + apply |
| `bash start.sh` / `stop.sh` | Lifecycle without re-rendering |
| `bash update.sh` | Pull submodule + images, re-apply, restart |
| `bash backup-bundle.sh` | Portable `.tar.gz` for VPS migration |
| `bash restore-bundle.sh` | Restore from portable bundle on a fresh VPS |
| `bash restore-borg.sh` | Restore from Borg (local or SFTP) on a fresh VPS |
| `bash backup.sh` | Run Borg backup now (when `backup.enabled`) |
| `bash restore.sh` | List or restore from Borg archives |
| `bash uninstall.sh` | Remove generated runtime files (keeps data) |

## deploy.yaml overview

See [`deploy.yaml.example`](deploy.yaml.example). Key sections:

- **opencloud** — domain, image tag, persistent `data_dir` / `config_dir` / `apps_dir`
- **proxy** — `caddy` with `mode: standalone` (default) or `integrate` (shared Caddy via [easydeploy-engine](../easydeploy-engine))
- **auth** — `builtin` (simple admin login) or `oidc` (external IdP)
- **weboffice** — `euro_office` or `collabora` (mutually exclusive with each other)
- **modules** — optional search, antivirus, radicale, monitoring
- **backup** — optional Borg backups to a local directory or SFTP repository

## Authentication modes

### Built-in (default)

Uses OpenCloud's built-in LDAP. Admin password is generated on first `apply.sh` (or set via wizard) and stored in `.opencloud-easy-deploy/secrets.yaml`.

### External OIDC (Authentik, Keycloak, …)

Set `auth.mode: oidc` and configure `auth.oidc` in `deploy.yaml`. The stack adds `idm/external-idp.yml` plus a local overlay for role mapping via `proxy.yaml`.

For **Authelia** on the same VPS (with easydeploy-engine), set `auth.oidc.provider: authelia` and follow [`docs/integrating-engine.md`](docs/integrating-engine.md).

#### Authentik setup

1. **Create groups** for OpenCloud roles:
   - `opencloud-admin`
   - `opencloud-user`
   - `opencloud-guest`

   Match names to `auth.oidc.role_mapping` in `deploy.yaml`.

2. **Create an OAuth2/OpenID provider** application:
   - **Slug:** `opencloud`
   - **Client type:** Public
   - **Redirect URIs / Origins (strict):**
     - `https://<OC_DOMAIN>/`
     - `https://<OC_DOMAIN>/oidc-callback.html`
     - `https://<OC_DOMAIN>/oidc-silent-redirect.html`
   - **Scopes:** `email`, `offline_access`, `openid`, `profile`
   - **Bindings:** attach the three groups above

3. **Set deploy.yaml** (example):

```yaml
auth:
  mode: oidc
  oidc:
    issuer_url: https://authentik.example.com/application/o/opencloud/
    account_url: https://authentik.example.com/if/user/
    domain: authentik.example.com
    client_id: opencloud
    client_scopes: "openid profile email offline_access"
    role_claim: groups
    role_mapping:
      admin: opencloud-admin
      user: opencloud-user
      guest: opencloud-guest
```

4. Run `bash apply.sh`.

#### Other OpenCloud clients (Authentik)

Configure separate OAuth clients in your IdP:

| Client | Client ID | Redirect URI / Origin |
|--------|-----------|------------------------|
| Android | `OpenCloudAndroid` | (strict) `oc://android.opencloud.eu` |
| iOS | `OpenCloudIOS` | (strict) `oc://ios.opencloud.eu` |
| Desktop | `OpenCloudDesktop` | (regex) `http://127.0.0.1.*`, `http://localhost.*` |

## Architecture

```
Internet → Caddy (:443, Let's Encrypt)
              ├── cloud.example.com      → opencloud:9200
              └── eurooffice.example.com → euro-office:80

opencloud-compose stack (docker network: opencloud-net)
  ├── opencloud
  ├── euro-office (optional)
  ├── ldap-server (OIDC mode only)
  └── optional modules (tika, clamav, …)
```

Upstream compose files live in the `opencloud-compose/` git submodule. Customizations are in `overlays/` and rendered configs — never edit the submodule directly. Update with:

```bash
bash update.sh
```

## Security defaults

| Setting | Test install script | opencloud-easy-deploy |
|---------|---------------------|------------------------|
| TLS | self-signed / insecure | Caddy + Let's Encrypt |
| `INSECURE` | `true` | `false` |
| `DEMO_USERS` | enabled | `false` |
| Secrets | hardcoded | generated, mode `0600` |
| Config | ephemeral | persistent paths outside submodule |

## Backups

Two ways to recover on a fresh VPS:

| Method | You provide | Everything else |
|--------|-------------|-----------------|
| **Portable bundle** | `opencloud-backup-*.tar.gz` | `bash restore-bundle.sh` |
| **Borg (local or SFTP)** | Passphrase + how to reach the repo (SFTP host/user/path + SSH key, or local repo path) | `deploy.yaml` and data come from the archive |

### Quick migration (recommended — ~5 minutes)

**On the running server** (stop OpenCloud first for a clean snapshot):

```bash
bash stop.sh
bash backup-bundle.sh
# → opencloud-backup-cloud.example.com-20260729T120000Z.tar.gz
```

Copy that single file to your new VPS (scp, rsync, object storage, etc.).

**On a fresh VPS:**

```bash
git clone <repo> opencloud-easy-deploy
cd opencloud-easy-deploy
bash restore-bundle.sh /path/to/opencloud-backup-*.tar.gz
```

That one command installs Docker, uv, restores all data/config/secrets, and runs `apply.sh`. Point DNS at the new server before visiting the URL.

The bundle contains **passwords and keys** — treat it like a secrets backup.

Optional output format: `bash backup-bundle.sh -o /tmp/backup.tar.zst` (requires `zstd`).

---

### Scheduled Borg backups (optional)

For incremental backups on the same server or off-site via SFTP, enable Borg in `deploy.yaml`:

**Local repository (simple, same VPS):**

```yaml
backup:
  enabled: true
  repository:
    type: local
    path: /var/backups/opencloud
  schedule:
    enabled: true
    calendar: "*-*-* 03:00:00"
    persistent: true
  retention:
    keep_daily: 7
    keep_weekly: 4
    keep_monthly: 6
    keep_yearly: 0
```

**SFTP off-site (recommended for crash recovery):**

```yaml
backup:
  enabled: true
  repository:
    type: sftp
    host: backup.example.com
    user: borg
    path: /repos/opencloud
    port: 22
    ssh_key_path: /root/.ssh/borg_backup
    host_key_check: true
  schedule:
    enabled: true
    calendar: "*-*-* 03:00:00"
  retention:
    keep_daily: 7
    keep_weekly: 4
    keep_monthly: 6
```

Each backup archive includes OpenCloud data, `deploy.yaml`, and `secrets.yaml` — restore pulls configuration from the archive, not a separate bootstrap file.

Then run `bash apply.sh` to generate backup config and a `BORG_PASSPHRASE` in `.opencloud-easy-deploy/secrets.yaml`. Store the passphrase and SSH private key safely off-site.

**Run a backup now:**

```bash
bash backup.sh
```

**List archives:**

```bash
bash restore.sh --list
```

**Restore on this host:**

```bash
bash stop.sh
bash restore.sh --latest
bash apply.sh
bash start.sh
```

**Restore on a fresh VPS from Borg:**

```bash
git clone <repo> opencloud-easy-deploy && cd opencloud-easy-deploy
export BORG_PASSPHRASE='your-passphrase'
# SFTP:
export OCD_BACKUP_SFTP_HOST=backup.example.com
export OCD_BACKUP_SFTP_USER=borg
export OCD_BACKUP_SFTP_PATH=/repos/opencloud
export OCD_BACKUP_SSH_KEY=/root/.ssh/borg_backup
# Or local repo path:
# export OCD_BACKUP_LOCAL_PATH=/var/backups/opencloud
bash restore-borg.sh
```

`restore-borg.sh` fetches `deploy.yaml` from the archive first, then restores all data using the paths defined there.

Backups include OpenCloud data/config/apps, LDAP state (if OIDC), Euro Office data, `deploy.yaml`, and `secrets.yaml`.

For scheduled backups, `apply.sh` writes systemd unit files to `.opencloud-easy-deploy/backup/systemd/`. Install them:

```bash
sudo cp .opencloud-easy-deploy/backup/systemd/opencloud-backup.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now opencloud-backup.timer
```

Store the Borg passphrase and SSH key safely off-site — without them, backups cannot be restored. For local repos, sync `/var/backups/opencloud` elsewhere or use `backup-bundle.sh` periodically. For SFTP, the remote repository *is* your off-site copy.

## Development

Python dependencies are managed with [uv](https://docs.astral.sh/uv/). Install uv, then:

```bash
uv sync --dev
uv run pytest tests
```

Apply without starting containers (render only):

```bash
bash apply.sh --no-reconcile-runtime
```

## Troubleshooting

### 502 Bad Gateway / WOPI discovery errors

If OpenCloud logs show `WopiDiscovery: wopi app url failed with unexpected code HttpCode=502`, common causes are:

1. **Caddy cannot reach backend containers** — Caddy runs in a separate Compose project from OpenCloud/Euro Office. This project sets stable `container_name` values (`opencloud`, `euro-office`) and generates a network overlay so Caddy can proxy to them on `opencloud-net`.

2. **Hairpin NAT / internal HTTPS calls** — OpenCloud fetches `https://<euro-office-domain>/hosting/discovery` from inside Docker. The generated overlay maps your public domains to `host-gateway` so those requests reach Caddy on the host without relying on NAT loopback.

3. **JWT mismatch** — Euro Office `JWT_SECRET` must match OpenCloud `COLLABORATION_WOPI_SECRET` (not `COLLABORATION_JWT_SECRET`, which breaks internal REVA tokens). Both are set from `.opencloud-easy-deploy/secrets.yaml` on apply. If JWT was wrong on first boot, remove `<data-root>/euro-office` and re-apply so Euro Office regenerates its persisted secrets.

4. **X-Frame-Options / iframe blocked** — If the browser console shows Euro Office blocked by `X-Frame-Options: sameorigin`, re-run `bash apply.sh` so Caddy sets `Content-Security-Policy: frame-ancestors` for the Euro Office domain instead.

Euro Office first boot can take **3–5 minutes** (fonts, caches). `apply.sh` waits for WOPI discovery before restarting OpenCloud.

After updating, recreate the stack (a plain `stop.sh` / `start.sh` is not enough when networking overlays change):

```bash
bash apply.sh
bash diagnose.sh
```

```bash
curl -fsS "https://eurooffice.example.com/hosting/discovery" | head
```

## License

See upstream [opencloud-compose](https://github.com/opencloud-eu/opencloud-compose) for OpenCloud licensing. This deployment tooling is provided as-is.
