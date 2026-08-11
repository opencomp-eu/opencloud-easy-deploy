# Integrating with easydeploy-engine

Use this when Authelia (or another kit) already runs on the same VPS behind **easydeploy-engine** on `easydeploy-net`.

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
    provider: authelia   # adds idm/external-authelia.yml overlay
    issuer_url: https://auth.example.com
    account_url: https://auth.example.com/
    domain: auth.example.com
    client_id: opencloud
    client_scopes: openid profile email offline_access
    role_claim: groups
    role_mapping:
      admin: opencloud-admin
      user: opencloud-user
      guest: opencloud-guest
```

## Authelia OIDC client

In Authelia `deploy.yaml`, add an OIDC client (after OpenCloud domain is known):

```yaml
oidc:
  clients:
    - id: opencloud
      description: OpenCloud
      secret: "<generated>"
      redirect_uris:
        - https://cloud.example.com/oidc-callback.html
      scopes:
        - openid
        - profile
        - email
        - offline_access
      grant_types:
        - authorization_code
      response_types:
        - code
      authorization_policy: two_factor
```

Re-run `bash apply.sh` in authelia-easy-deploy, then opencloud-easy-deploy, then easydeploy-engine.

## Apply order

1. Engine + Authelia in integrate mode (see authelia-easy-deploy `docs/integrating-engine.md`).
2. Clone/configure opencloud-easy-deploy; set `proxy.mode: integrate` and OIDC as above.
3. `bash apply.sh` in opencloud-easy-deploy.
4. Register OpenCloud in `engine.yaml` (fragment path `.opencloud-easy-deploy/integration/caddy.caddy`).
5. `bash apply.sh` in easydeploy-engine.

Standalone OpenCloud Caddy (`opencloud_caddy`) is not started in integrate mode.
