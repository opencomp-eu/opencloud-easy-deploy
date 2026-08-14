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
    provider: authelia   # adds Authelia-specific compose overlay
    issuer_url: https://auth.example.com
    account_url: https://auth.example.com/
    domain: auth.example.com
    client_id: opencloud
    client_scopes: openid profile email groups
    role_claim: groups
    role_mapping:
      admin: opencloud-admin
      user: opencloud-user
      guest: opencloud-guest
```

## Authelia OIDC client

OpenCloud's **browser** login uses a **public** OIDC client with PKCE (no client secret). The client ID must match `auth.oidc.client_id` in OpenCloud (`opencloud` above).

In Authelia `deploy.yaml`:

```yaml
oidc:
  enabled: true
  clients:
    - client_id: opencloud
      client_name: OpenCloud
      public: true
      authorization_policy: two_factor
      require_pkce: true
      pkce_challenge_method: S256
      token_endpoint_auth_method: none
      redirect_uris:
        - https://cloud.example.com/
        - https://cloud.example.com/web-oidc-callback
        - https://cloud.example.com/oidc-callback.html
        - https://cloud.example.com/oidc-silent-redirect.html
      scopes:
        - openid
        - offline_access
        - groups
        - profile
        - email
      grant_types:
        - authorization_code
        - refresh_token
      response_types:
        - code
```

Give your user the `opencloud-admin` group in Authelia `deploy.yaml` (`users:` section), not by editing `users_database.yml` directly.

Authelia must also allow browser CORS for the token/userinfo endpoints (OpenCloud Web POSTs the auth code from `https://cloud…` to `https://auth…`). `apply.sh` in authelia-easy-deploy now writes this automatically:

```yaml
identity_providers:
  oidc:
    cors:
      endpoints:
        - authorization
        - token
        - revocation
        - userinfo
        - introspection
      allowed_origins_from_client_redirect_uris: true
```

Re-run `bash apply.sh` in authelia-easy-deploy, then opencloud-easy-deploy, then easydeploy-engine.

Official reference: [Authelia — openCloud client](https://www.authelia.com/integration/openid-connect/clients/opencloud/)

## Apply order

1. Engine + Authelia in integrate mode (see authelia-easy-deploy `docs/integrating-engine.md`).
2. Clone/configure opencloud-easy-deploy; set `proxy.mode: integrate` and OIDC as above.
3. `bash apply.sh` in opencloud-easy-deploy.
4. Register OpenCloud in `engine.yaml` (fragment path `.opencloud-easy-deploy/integration/caddy.caddy`).
5. `bash apply.sh` in easydeploy-engine.

Standalone OpenCloud Caddy (`opencloud_caddy`) is not started in integrate mode.
