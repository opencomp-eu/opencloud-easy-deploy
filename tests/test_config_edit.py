"""Tests for OpenCloud wizard config helpers."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.config_edit import discover_local_authelia, emit_local_authelia, read_authelia_domain


def test_read_authelia_domain(tmp_path: Path):
    deploy = tmp_path / "deploy.yaml"
    deploy.write_text(yaml.safe_dump({"authelia": {"domain": "auth.opencomp.eu"}}))
    assert read_authelia_domain(deploy) == "auth.opencomp.eu"
    assert read_authelia_domain(tmp_path / "missing.yaml") == ""


def test_discover_local_authelia_sibling(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("EASYDEPLOY_AUTHELIA_DEPLOY", raising=False)
    monkeypatch.delenv("EASYDEPLOY_AUTHELIA_DOMAIN", raising=False)
    opencloud = tmp_path / "opencloud-easy-deploy"
    authelia = tmp_path / "authelia-easy-deploy"
    opencloud.mkdir()
    authelia.mkdir()
    (authelia / "deploy.yaml").write_text(
        yaml.safe_dump({"authelia": {"domain": "auth.opencomp.eu", "sso_domain": "opencomp.eu"}})
    )

    found = discover_local_authelia(opencloud)
    assert found["domain"] == "auth.opencomp.eu"
    assert found["deploy"].endswith("authelia-easy-deploy/deploy.yaml")


def test_discover_local_authelia_env_path(tmp_path: Path, monkeypatch):
    opencloud = tmp_path / "opencloud-easy-deploy"
    opencloud.mkdir()
    deploy = tmp_path / "elsewhere" / "deploy.yaml"
    deploy.parent.mkdir()
    deploy.write_text(yaml.safe_dump({"authelia": {"domain": "auth.other.example"}}))
    monkeypatch.setenv("EASYDEPLOY_AUTHELIA_DEPLOY", str(deploy))

    found = discover_local_authelia(opencloud)
    assert found["domain"] == "auth.other.example"


def test_discover_local_authelia_env_domain_only(tmp_path: Path, monkeypatch):
    opencloud = tmp_path / "opencloud-easy-deploy"
    opencloud.mkdir()
    monkeypatch.setenv("EASYDEPLOY_AUTHELIA_DOMAIN", "auth.env.example")
    monkeypatch.delenv("EASYDEPLOY_AUTHELIA_DEPLOY", raising=False)

    found = discover_local_authelia(opencloud)
    assert found["domain"] == "auth.env.example"


def test_emit_local_authelia_empty(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("EASYDEPLOY_AUTHELIA_DEPLOY", raising=False)
    monkeypatch.delenv("EASYDEPLOY_AUTHELIA_DOMAIN", raising=False)
    opencloud = tmp_path / "opencloud-easy-deploy"
    opencloud.mkdir()
    text = emit_local_authelia(opencloud)
    assert "LOCAL_AUTHELIA_DOMAIN=''" in text or "LOCAL_AUTHELIA_DOMAIN=" in text
