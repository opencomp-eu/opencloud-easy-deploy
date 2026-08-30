# Integrating with easydeploy-engine

Use this when Kanidm already runs on the same VPS behind **easydeploy-engine** on `easydeploy-net`.

## deploy.yaml

```yaml
proxy:
  type: caddy
  mode: integrate
  integrate:
    network: easydeploy-net

opencloud:
  domain: cloud.example.com
  # ...

auth:
  mode: oidc
  oidc:
    provider: kanidm   # adds Kanidm-specific compose overlay
    issuer_url: https://idm.example.com/oauth2/openid/opencloud
    account_url: https://idm.example.com/
    domain: idm.example.com
    client_id: opencloud
    client_scopes: openid profile email groups groups_name
    role_claim: groups
    role_mapping:
      admin: opencloud-admin
      user: opencloud-user
      guest: opencloud-guest
```

Kanidm uses a **per-client** issuer (`/oauth2/openid/<client_id>`), not the portal origin.

## Kanidm OIDC client

OpenCloud's **browser** login uses a **public** OIDC client with PKCE (no client secret). The client ID must match `auth.oidc.client_id` in OpenCloud (`opencloud` above).

On a same-VPS engine install you can skip registering the client by hand: `bash wizard.sh` in easydeploy-engine clones this repo if needed and writes the Kanidm OIDC sidecar. Kanidm apply then creates the public client and default groups (`opencloud-admin`, `opencloud-user`, `opencloud-guest`).

Give your user the `opencloud-admin` group in Kanidm, not by creating a local OpenCloud account.

## Apply order

1. Engine + Kanidm in integrate mode (see kanidm-easy-deploy `docs/integrating-engine.md`).
2. Clone/configure opencloud-easy-deploy; set `proxy.mode: integrate` and OIDC as above.
3. `bash apply.sh` in opencloud-easy-deploy.
4. Register OpenCloud in `engine.yaml` (fragment path `.opencloud-easy-deploy/integration/caddy.caddy`).
5. `bash apply.sh` in easydeploy-engine.

Standalone OpenCloud Caddy (`opencloud_caddy`) is not started in integrate mode.
