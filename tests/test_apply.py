"""Tests for scripts/apply.py."""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path

import pytest
import yaml

from scripts.apply import (
    _network_address,
    apply_engine_oidc_sidecar,
    bootstrap_ldap_tls,
    build_env_vars,
    build_proxy_role_block,
    derive_compose_files,
    discover_ldap_server_ip,
    ldap_data_dir,
    opencloud_admin_user_id,
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
            "tag": "7.5.0",
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


def test_derive_compose_files_oidc_kanidm_provider():
    config = _base_config(
        auth={
            "mode": "oidc",
            "oidc": {
                "provider": "kanidm",
                "issuer_url": "https://idm.example/oauth2/openid/opencloud",
                "account_url": "https://idm.example/",
                "domain": "idm.example",
                "client_id": "opencloud",
            },
        },
    )
    files = derive_compose_files(config)
    assert "idm/external-idp.yml" in files
    assert "../overlays/idm/kanidm-provider.yml" in files
    assert "../overlays/idm/authelia-provider.yml" not in files
    assert "idm/external-authelia.yml" not in files


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
    expected_uid_gid = (
        "1000:1000" if os.geteuid() == 0 else f"{os.getuid()}:{os.getgid()}"
    )
    assert env["OC_CONTAINER_UID_GID"] == expected_uid_gid
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
    assert env["GRAPH_ASSIGN_DEFAULT_USER_ROLE"] == "false"
    assert env["IDP_ISSUER_URL"] == "https://idp.example/o/opencloud/"
    assert "idm/external-idp.yml" in env["COMPOSE_FILE"]


def test_build_env_vars_kanidm_uses_groups_name_scopes():
    config = _base_config(
        auth={
            "mode": "oidc",
            "oidc": {
                "provider": "kanidm",
                "issuer_url": "https://idm.example/oauth2/openid/opencloud",
                "account_url": "https://idm.example/",
                "domain": "idm.example",
                "client_id": "opencloud",
                "role_claim": "opencloudRoles",
                "role_mapping": {"admin": "admin", "user": "user", "guest": "guest"},
            },
        }
    )
    env = build_env_vars(
        config,
        {
            "INITIAL_ADMIN_PASSWORD": "x",
            "EURO_OFFICE_JWT_SECRET": "y",
            "LDAP_BIND_PASSWORD": "z",
        },
    )
    assert env["OC_OIDC_CLIENT_SCOPES"] == "openid profile email groups groups_name"
    assert "PROXY_ROLE_ASSIGNMENT_OIDC_CLAIM" not in env
    assert env["PROXY_ROLE_ASSIGNMENT_DRIVER"] == "default"
    assert env["GRAPH_ASSIGN_DEFAULT_USER_ROLE"] == "true"
    assert env["OC_LDAP_DISABLE_USER_MECHANISM"] == "none"
    assert "../overlays/idm/kanidm-provider.yml" in env["COMPOSE_FILE"]
    assert "../overlays/idm/authelia-provider.yml" not in env["COMPOSE_FILE"]


def test_build_proxy_role_block_kanidm_uses_default_driver():
    config = _base_config(
        auth={
            "mode": "oidc",
            "oidc": {
                "provider": "kanidm",
                "role_claim": "opencloudRoles",
                "role_mapping": {"admin": "admin"},
            },
        }
    )
    block = build_proxy_role_block(config)
    assert "driver: default" in block
    assert "oidc_role_mapper" not in block


def test_opencloud_admin_user_id_from_sibling_kanidm(tmp_path, monkeypatch):
    from scripts import apply as apply_module

    sibling = tmp_path / "kanidm-easy-deploy"
    sibling.mkdir()
    (sibling / "deploy.yaml").write_text(
        yaml.safe_dump(
            {
                "users": [
                    {"username": "thomas", "groups": ["opencloud-admin", "mail-users"]},
                ]
            }
        )
    )
    monkeypatch.setattr(apply_module, "PROJECT_ROOT", tmp_path / "opencloud-easy-deploy")
    config = _base_config(auth={"mode": "oidc", "oidc": {"provider": "kanidm"}})
    assert opencloud_admin_user_id(config) == "thomas"


def test_opencloud_admin_user_id_explicit_wins():
    config = _base_config(
        auth={"mode": "oidc", "oidc": {"provider": "kanidm", "admin_user": "operator"}}
    )
    assert opencloud_admin_user_id(config) == "operator"


def test_ldap_data_dir_is_sibling_of_config():
    config = _base_config()
    assert ldap_data_dir(config) == Path("/var/lib/opencloud/ldap_data")


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
    assert "ldap-server" not in data["services"]
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


def test_render_network_overlay_dual_homes_ldap_server(tmp_path, monkeypatch):
    from scripts import apply as apply_module

    overlay_path = tmp_path / "network-fixups.yml"
    monkeypatch.setattr(apply_module, "NETWORK_OVERLAY_PATH", overlay_path)
    render_network_overlay(
        _base_config(
            proxy={"type": "caddy", "mode": "integrate"},
            auth={
                "mode": "oidc",
                "oidc": {"domain": "auth.test.example"},
            },
        )
    )
    data = yaml.safe_load(overlay_path.read_text())
    ldap = data["services"]["ldap-server"]
    assert ldap["container_name"] == "ldap-server"
    assert ldap["networks"] == ["opencloud-net"]
    assert data["services"]["opencloud"]["depends_on"] == ["ldap-server"]
    assert data["services"]["opencloud"]["links"] == ["ldap-server"]
    assert data["services"]["opencloud"]["networks"] == ["opencloud-net", "easydeploy-net"]


def test_render_network_overlay_pins_ldap_ip(tmp_path, monkeypatch):
    from scripts import apply as apply_module

    overlay_path = tmp_path / "network-fixups.yml"
    monkeypatch.setattr(apply_module, "NETWORK_OVERLAY_PATH", overlay_path)
    render_network_overlay(
        _base_config(
            auth={"mode": "oidc", "oidc": {"domain": "auth.test.example"}},
        ),
        ldap_ip="172.20.0.7",
    )
    data = yaml.safe_load(overlay_path.read_text())
    assert "ldap-server:172.20.0.7" in data["services"]["opencloud"]["extra_hosts"]


def test_network_address_skips_invalid_placeholder():
    assert _network_address({"IPAddress": "invalid IP"}) == ""
    assert _network_address({"IPAddress": "172.20.0.7"}) == "172.20.0.7"
    assert _network_address({"IPAddress": "", "GlobalIPv6Address": "fd00::7"}) == "fd00::7"


def test_discover_ldap_server_ip_reads_bridge_membership(monkeypatch):
    payload = [
        {
            "Containers": {
                "abc": {"Name": "ldap-server", "IPv4Address": "172.21.0.9/16"},
            }
        }
    ]

    def fake_run(cmd, **_kwargs):
        assert cmd[:3] == ["docker", "network", "inspect"]
        return type("R", (), {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""})()

    monkeypatch.setattr("scripts.apply.subprocess.run", fake_run)
    assert discover_ldap_server_ip() == "172.21.0.9"


def test_discover_ldap_server_ip_falls_back_to_container_inspect(monkeypatch):
    inspect = [
        {
            "NetworkSettings": {
                "Networks": {
                    "easydeploy-net": {"IPAddress": "172.21.0.4"},
                    "opencloud-net": {"IPAddress": "172.18.0.8"},
                }
            }
        }
    ]

    def fake_run(cmd, **_kwargs):
        if cmd[:3] == ["docker", "network", "inspect"]:
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        return type("R", (), {"returncode": 0, "stdout": json.dumps(inspect), "stderr": ""})()

    monkeypatch.setattr("scripts.apply.subprocess.run", fake_run)
    assert discover_ldap_server_ip() == "172.18.0.8"


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


def test_apply_engine_oidc_sidecar_fills_blank_fields(tmp_path):
    sidecar = tmp_path / "oidc-provider.yaml"
    sidecar.write_text(
        "provider: kanidm\nissuer_url: https://idm.test.example/oauth2/openid/opencloud\n"
        "account_url: https://idm.test.example/\ndomain: idm.test.example\n"
        "client_id: opencloud\n"
    )
    config = {"auth": {"mode": "builtin", "oidc": {}}}
    apply_engine_oidc_sidecar(config, sidecar)
    assert config["auth"]["mode"] == "oidc"
    assert config["auth"]["oidc"]["issuer_url"] == "https://idm.test.example/oauth2/openid/opencloud"
    assert config["auth"]["oidc"]["provider"] == "kanidm"


def test_apply_engine_oidc_sidecar_replaces_stale_managed_values(tmp_path):
    sidecar = tmp_path / "oidc-provider.yaml"
    sidecar.write_text(
        "provider: kanidm\n"
        "issuer_url: https://auth.test.example/oauth2/openid/opencloud\n"
        "account_url: https://auth.test.example/\n"
        "domain: auth.test.example\n"
        "client_id: opencloud\n"
    )
    config = {
        "auth": {
            "mode": "builtin",
            "oidc": {
                "provider": "kanidm",
                "issuer_url": "https://idm.example.com/oauth2/openid/opencloud",
                "domain": "idm.example.com",
                "client_id": "old-client",
            },
        }
    }
    apply_engine_oidc_sidecar(config, sidecar)
    assert config["auth"]["mode"] == "oidc"
    assert config["auth"]["oidc"]["issuer_url"] == (
        "https://auth.test.example/oauth2/openid/opencloud"
    )
    assert config["auth"]["oidc"]["domain"] == "auth.test.example"
    assert config["auth"]["oidc"]["client_id"] == "opencloud"


def test_apply_engine_oidc_sidecar_respects_external_provider(tmp_path):
    sidecar = tmp_path / "oidc-provider.yaml"
    sidecar.write_text("provider: kanidm\nissuer_url: https://idm.test.example/oauth2/openid/opencloud\n")
    config = {"auth": {"mode": "oidc", "oidc": {"provider": "keycloak", "issuer_url": "https://idp.example"}}}
    apply_engine_oidc_sidecar(config, sidecar)
    assert config["auth"]["oidc"]["issuer_url"] == "https://idp.example"
