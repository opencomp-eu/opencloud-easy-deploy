"""Tests for OpenCloud wizard config helpers."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.config_edit import discover_local_kanidm, emit_local_kanidm, read_kanidm_domain


def test_read_kanidm_domain(tmp_path: Path):
    deploy = tmp_path / "deploy.yaml"
    deploy.write_text(yaml.safe_dump({"kanidm": {"domain": "idm.opencomp.eu"}}))
    assert read_kanidm_domain(deploy) == "idm.opencomp.eu"
    assert read_kanidm_domain(tmp_path / "missing.yaml") == ""


def test_discover_local_kanidm_sibling(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("EASYDEPLOY_KANIDM_DEPLOY", raising=False)
    monkeypatch.delenv("EASYDEPLOY_KANIDM_DOMAIN", raising=False)
    opencloud = tmp_path / "opencloud-easy-deploy"
    kanidm = tmp_path / "kanidm-easy-deploy"
    opencloud.mkdir()
    kanidm.mkdir()
    (kanidm / "deploy.yaml").write_text(
        yaml.safe_dump({"kanidm": {"domain": "idm.opencomp.eu"}})
    )

    found = discover_local_kanidm(opencloud)
    assert found["domain"] == "idm.opencomp.eu"
    assert found["deploy"].endswith("kanidm-easy-deploy/deploy.yaml")


def test_discover_local_kanidm_env_path(tmp_path: Path, monkeypatch):
    opencloud = tmp_path / "opencloud-easy-deploy"
    opencloud.mkdir()
    deploy = tmp_path / "elsewhere" / "deploy.yaml"
    deploy.parent.mkdir()
    deploy.write_text(yaml.safe_dump({"kanidm": {"domain": "idm.other.example"}}))
    monkeypatch.setenv("EASYDEPLOY_KANIDM_DEPLOY", str(deploy))

    found = discover_local_kanidm(opencloud)
    assert found["domain"] == "idm.other.example"


def test_discover_local_kanidm_env_domain_only(tmp_path: Path, monkeypatch):
    opencloud = tmp_path / "opencloud-easy-deploy"
    opencloud.mkdir()
    monkeypatch.setenv("EASYDEPLOY_KANIDM_DOMAIN", "idm.env.example")
    monkeypatch.delenv("EASYDEPLOY_KANIDM_DEPLOY", raising=False)

    found = discover_local_kanidm(opencloud)
    assert found["domain"] == "idm.env.example"


def test_emit_local_kanidm_empty(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("EASYDEPLOY_KANIDM_DEPLOY", raising=False)
    monkeypatch.delenv("EASYDEPLOY_KANIDM_DOMAIN", raising=False)
    opencloud = tmp_path / "opencloud-easy-deploy"
    opencloud.mkdir()
    text = emit_local_kanidm(opencloud)
    assert "LOCAL_KANIDM_DOMAIN=''" in text or "LOCAL_KANIDM_DOMAIN=" in text
