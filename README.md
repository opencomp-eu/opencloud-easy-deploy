# OpenCloud Easy Deploy

Production-oriented [OpenCloud](https://opencloud.eu) deployment for VPS hosts. One `deploy.yaml` describes your stack; `apply.sh` renders configs, manages secrets, and starts containers with **Caddy** (TLS) and upstream **[opencloud-compose](https://github.com/opencloud-eu/opencloud-compose)** (unchanged).

This replaces the official test installer (`curl -L https://opencloud.eu/install | bash`), which runs an insecure localhost binary and prints *"This is a fragile test setup, not suitable for production!"*

## Quick start

**Requirements:** Linux VPS, Docker Compose v2, DNS pointing at the server, ports 80/443 open.

```bash
git clone https://github.com/your-org/opencloud-easy-deploy.git
cd opencloud-easy-deploy
git submodule update --init --recursive
uv sync --dev

bash wizard.sh    # interactive: writes deploy.yaml and deploys
```

Or manually:

```bash
cp deploy.yaml.example deploy.yaml
# edit deploy.yaml — set your domain and paths
bash apply.sh
```

## Workflow

| File / script | Role |
|---------------|------|
| `deploy.yaml` | Operator-owned desired state (commit this) |
| `.opencloud-easy-deploy/secrets.yaml` | Generated passwords (gitignored) |
| `opencloud-compose/.env` | Generated Docker Compose env (gitignored) |
| `bash apply.sh` | Converge config and start/restart services |
| `bash wizard.sh` | Interactive `deploy.yaml` creator + apply |
| `bash start.sh` / `stop.sh` | Lifecycle without re-rendering |
| `bash update.sh` | Pull submodule + images, re-apply, restart |
| `bash uninstall.sh` | Remove generated runtime files (keeps data) |

## deploy.yaml overview

See [`deploy.yaml.example`](deploy.yaml.example). Key sections:

- **opencloud** — domain, image tag, persistent `data_dir` / `config_dir` / `apps_dir`
- **proxy** — `caddy` (only option in v1)
- **auth** — `builtin` (simple admin login) or `oidc` (external IdP)
- **weboffice** — `euro_office` or `collabora` (mutually exclusive with each other)
- **modules** — optional search, antivirus, radicale, monitoring

## Authentication modes

### Built-in (default)

Uses OpenCloud's built-in LDAP. Admin password is generated on first `apply.sh` (or set via wizard) and stored in `.opencloud-easy-deploy/secrets.yaml`.

### External OIDC (Authentik, Keycloak, …)

Set `auth.mode: oidc` and configure `auth.oidc` in `deploy.yaml`. The stack adds `idm/external-idp.yml` plus a local overlay for role mapping via `proxy.yaml`.

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

## License

See upstream [opencloud-compose](https://github.com/opencloud-eu/opencloud-compose) for OpenCloud licensing. This deployment tooling is provided as-is.
