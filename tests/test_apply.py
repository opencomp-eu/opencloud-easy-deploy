"""Tests for scripts/apply.py."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from scripts.apply import (
    bootstrap_ldap_tls,
    build_env_vars,
    derive_compose_files,
    render_caddyfile,
    render_network_overlay,
    render_template,
    validate_config,
)


def _base_config(**overrides) -> dict:
    config = {
        "opencloud": {
            "domain": "cloud.test.example",
            "image": "opencloudeu/opencloud-rolling",
            "tag": "7.2.0",
            "data_dir": "/var/lib/opencloud/data",
            "config_dir": "/var/lib/opencloud/config",
            "apps_dir": "/var/lib/opencloud/apps",
            "language": "en",
        },
        "proxy": {"type": "caddy", "mode": "standalone", "integrate": {"network": "easydeploy-net"}},
        "auth": {"mode": "builtin"},
        "weboffice": {
            "enabled": True,
            "type": "euro_office",
            "domain": "eurooffice.test.example",
        },
        "modules": {
            "search": False,
            "antivirus": False,
            "radicale": False,
            "monitoring": False,
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and key in config and isinstance(config[key], dict):
            config[key].update(value)
        else:
            config[key] = value
    return config


def test_derive_compose_files_integrate_excludes_caddy():
    config = _base_config(proxy={"type": "caddy", "mode": "integrate"})
    files = derive_compose_files(config)
    assert "../overlays/proxy/caddy.yml" not in files
    assert "docker-compose.yml" in files


def test_derive_compose_files_oidc_authelia_provider():
    config = _base_config(
        auth={
            "mode": "oidc",
            "oidc": {
                "provider": "authelia",
                "issuer_url": "https://auth.example/o",
                "account_url": "https://auth.example/",
                "domain": "auth.example",
                "client_id": "opencloud",
            },
        },
    )
    files = derive_compose_files(config)
    assert "idm/external-authelia.yml" in files
    assert "../overlays/idm/authelia-provider.yml" in files


def test_render_integration_fragment(tmp_path, monkeypatch):
    from scripts.apply import INTEGRATION_CADDY_FRAGMENT, render_integration_fragment

    monkeypatch.setattr("scripts.apply.INTEGRATION_DIR", tmp_path)
    monkeypatch.setattr("scripts.apply.INTEGRATION_CADDY_FRAGMENT", tmp_path / "caddy.caddy")
    render_integration_fragment(_base_config())
    text = (tmp_path / "caddy.caddy").read_text()
    assert "cloud.test.example" in text
    assert "eurooffice.test.example" in text


def test_derive_compose_files_builtin_euro_office():
    files = derive_compose_files(_base_config())
    assert files[0] == "docker-compose.yml"
    assert "../overlays/proxy/caddy.yml" in files
    assert "external-proxy/opencloud.yml" in files
    assert "weboffice/euro-office.yml" in files
    assert "external-proxy/euro-office.yml" in files
    assert "../overlays/weboffice/euro-office-production.yml" in files
    assert "../.opencloud-easy-deploy/compose/network-fixups.yml" in files
    assert "idm/external-idp.yml" not in files


def test_derive_compose_files_oidc_with_modules():
    config = _base_config(
        auth={
            "mode": "oidc",
            "oidc": {
                "issuer_url": "https://idp.example/o/opencloud/",
                "account_url": "https://idp.example/if/user/",
                "domain": "idp.example",
                "client_id": "opencloud",
            },
        },
        modules={"search": True, "antivirus": True, "radicale": False, "monitoring": True},
    )
    files = derive_compose_files(config)
    assert "idm/external-idp.yml" in files
    assert "../overlays/idm/oidc-external.yml" in files
    assert "search/tika.yml" in files
    assert "antivirus/clamav.yml" in files
    assert "monitoring/monitoring.yml" in files


def test_derive_compose_files_collabora():
    config = _base_config(weboffice={"enabled": True, "type": "collabora", "domain": "office.test.example"})
    files = derive_compose_files(config)
    assert "weboffice/collabora.yml" in files
    assert "external-proxy/collabora.yml" in files


def test_validate_rejects_example_domain():
    with pytest.raises(ValueError, match="opencloud.domain"):
        validate_config(_base_config(opencloud={"domain": "cloud.example.com"}))


def test_validate_oidc_requires_issuer():
    config = _base_config(auth={"mode": "oidc", "oidc": {"client_id": "x"}})
    with pytest.raises(ValueError, match="issuer_url"):
        validate_config(config)


def test_build_env_vars_production_defaults():
    secrets = {
        "INITIAL_ADMIN_PASSWORD": "secret-admin",
        "EURO_OFFICE_JWT_SECRET": "jwt-secret",
        "LDAP_BIND_PASSWORD": "ldap-pass",
    }
    env = build_env_vars(_base_config(), secrets)
    assert env["INSECURE"] == "false"
    assert env["DEMO_USERS"] == "false"
    assert env["OC_DOMAIN"] == "cloud.test.example"
    assert env["EURO_OFFICE_DOMAIN"] == "eurooffice.test.example"
    assert env["EURO_OFFICE_JWT_SECRET"] == "jwt-secret"
    assert env["EURO_OFFICE_DATA_DIR"] == "/var/lib/opencloud/euro-office"
    assert env["OCD_CADDYFILE"].endswith("/caddy/Caddyfile")
    assert "idm/external-idp.yml" not in env["COMPOSE_FILE"]


def test_build_env_vars_oidc():
    config = _base_config(
        auth={
            "mode": "oidc",
            "oidc": {
                "issuer_url": "https://idp.example/o/opencloud/",
                "account_url": "https://idp.example/if/user/",
                "domain": "idp.example",
                "client_id": "opencloud",
                "role_claim": "groups",
                "role_mapping": {"admin": "admins", "user": "users", "guest": "guests"},
            },
        }
    )
    secrets = {
        "INITIAL_ADMIN_PASSWORD": "x",
        "EURO_OFFICE_JWT_SECRET": "y",
        "LDAP_BIND_PASSWORD": "z",
    }
    env = build_env_vars(config, secrets)
    assert env["PROXY_ROLE_ASSIGNMENT_DRIVER"] == "oidc"
    assert env["IDP_ISSUER_URL"] == "https://idp.example/o/opencloud/"
    assert "idm/external-idp.yml" in env["COMPOSE_FILE"]


def test_render_proxy_role_template():
    template = Path("config-templates/opencloud/proxy.yaml.template").read_text()
    rendered = render_template(
        template,
        {
            "ROLE_CLAIM": "groups",
            "ROLE_ADMIN": "opencloud-admin",
            "ROLE_USER": "opencloud-user",
            "ROLE_GUEST": "opencloud-guest",
        },
    )
    assert "role_claim: groups" in rendered
    assert "claim_value: opencloud-admin" in rendered


def test_antivirus_adds_start_additional_services():
    config = _base_config(modules={"search": False, "antivirus": True, "radicale": False, "monitoring": False})
    env = build_env_vars(
        config,
        {
            "INITIAL_ADMIN_PASSWORD": "a",
            "EURO_OFFICE_JWT_SECRET": "b",
            "LDAP_BIND_PASSWORD": "c",
        },
    )
    assert env["START_ADDITIONAL_SERVICES"] == "antivirus"


def test_render_network_overlay_sets_container_names(tmp_path, monkeypatch):
    from scripts import apply as apply_module

    overlay_path = tmp_path / "network-fixups.yml"
    monkeypatch.setattr(apply_module, "NETWORK_OVERLAY_PATH", overlay_path)

    render_network_overlay(_base_config())
    data = yaml.safe_load(overlay_path.read_text())

    assert data["services"]["opencloud"]["container_name"] == "opencloud"
    assert data["services"]["euro-office"]["container_name"] == "euro-office"
    assert data["networks"]["opencloud-net"]["external"] is True
    assert "eurooffice.test.example:host-gateway" in data["services"]["opencloud"]["extra_hosts"]
    assert "cloud.test.example:host-gateway" in data["services"]["euro-office"]["extra_hosts"]


def test_render_network_overlay_adds_idp_host_gateway(tmp_path, monkeypatch):
    from scripts import apply as apply_module

    overlay_path = tmp_path / "network-fixups.yml"
    monkeypatch.setattr(apply_module, "NETWORK_OVERLAY_PATH", overlay_path)
    render_network_overlay(
        _base_config(
            auth={
                "mode": "oidc",
                "oidc": {"domain": "auth.test.example"},
            }
        )
    )
    data = yaml.safe_load(overlay_path.read_text())
    assert "auth.test.example:host-gateway" in data["services"]["opencloud"]["extra_hosts"]


def test_render_caddyfile_allows_opencloud_iframe(tmp_path, monkeypatch):
    from scripts import apply as apply_module

    caddyfile = tmp_path / "Caddyfile"
    monkeypatch.setattr(apply_module, "CADDYFILE", caddyfile)
    monkeypatch.setattr(
        apply_module,
        "CADDY_TEMPLATE",
        tmp_path / "template",
    )
    apply_module.CADDY_TEMPLATE.write_text("{{OC_DOMAIN_BLOCK}}\n{{EURO_OFFICE_DOMAIN_BLOCK}}")

    render_caddyfile(_base_config())
    rendered = caddyfile.read_text()
    assert "frame-ancestors 'self' https://cloud.test.example" in rendered


def test_bootstrap_ldap_tls_creates_cert_files(tmp_path):
    certs_dir = tmp_path / "ldap_certs"
    bootstrap_ldap_tls(certs_dir)
    assert (certs_dir / "openldap.key").is_file()
    assert (certs_dir / "openldap.crt").is_file()
    bootstrap_ldap_tls(certs_dir)
