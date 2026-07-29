"""Tests for scripts/apply.py."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from scripts.apply import (
    build_env_vars,
    derive_compose_files,
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
        "proxy": {"type": "caddy"},
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


def test_derive_compose_files_builtin_euro_office():
    files = derive_compose_files(_base_config())
    assert files[0] == "docker-compose.yml"
    assert "external-proxy/opencloud.yml" in files
    assert "weboffice/euro-office.yml" in files
    assert "external-proxy/euro-office.yml" in files
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
