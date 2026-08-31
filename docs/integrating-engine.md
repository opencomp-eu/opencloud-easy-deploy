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
```

Kanidm uses a **per-client** issuer (`/oauth2/openid/<client_id>`), not the portal origin. Role assignment uses the default `user` role at login (`PROXY_ROLE_ASSIGNMENT_DRIVER=default`); put the operator in Kanidm group `opencloud-admin` so they become `OC_ADMIN_USER_ID`. Do not rely on `role_claim` / `opencloudRoles` for Kanidm.

## Kanidm OIDC client

OpenCloud's **browser** login uses a **public** OIDC client with PKCE (no client secret). The client ID must match `auth.oidc.client_id` in OpenCloud (`opencloud` above).

On a same-VPS engine install you can skip registering the client by hand: `bash wizard.sh` in easydeploy-engine clones this repo if needed and writes the Kanidm OIDC sidecar. Kanidm apply then creates the public client and default groups (`opencloud-admin`, `opencloud-user`, `opencloud-guest`).

Give your user the `opencloud-admin` group in Kanidm, not by creating a local OpenCloud account. OpenCloud's bundled OpenLDAP is only the local graph store; wipe it with `bash apply.sh --wipe-local-accounts` if a failed first login left a conflicting user (`/access-denied` after a successful Kanidm grant).

## Apply order

1. Configure Kanidm and OpenCloud, then enable both in `engine.yaml`.
2. Run `bash apply.sh` in easydeploy-engine.
3. The engine writes both OIDC sidecars, applies Kanidm to register the
   `opencloud` client, applies OpenCloud to consume the provider configuration,
   and reloads shared Caddy.

Do not use `--skip-kits` for the initial identity wiring: that writes sidecars
but does not register the client or restart OpenCloud.

Standalone OpenCloud Caddy (`opencloud_caddy`) is not started in integrate mode.
