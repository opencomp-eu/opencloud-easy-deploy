"""Tests for scripts/bundle.py."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.bundle import cmd_create, cmd_restore
from scripts.backup import payload_arcname


def test_payload_arcname_mapping():
    assert payload_arcname("/backup-root/data") == "payload/data"
    assert payload_arcname("/backup-root/secrets/secrets.yaml") == "payload/secrets/secrets.yaml"


def test_bundle_roundtrip(tmp_path, monkeypatch):
    from scripts import backup as backup_module
    from scripts import bundle as bundle_module

    project = tmp_path / "project"
    state = project / ".opencloud-easy-deploy"
    data_dir = tmp_path / "var" / "lib" / "opencloud" / "data"
    config_dir = tmp_path / "var" / "lib" / "opencloud" / "config"
    apps_dir = tmp_path / "var" / "lib" / "opencloud" / "apps"
    ldap_base = tmp_path / "var" / "lib" / "opencloud"

    for path in (data_dir, config_dir, apps_dir, ldap_base / "ldap_certs", ldap_base / "ldap_data", state):
        path.mkdir(parents=True)
    (data_dir / "hello.txt").write_text("world\n")
    (state / "secrets.yaml").write_text("INITIAL_ADMIN_PASSWORD: secret\n")
    deploy = project / "deploy.yaml"
    deploy.write_text(
        yaml.safe_dump(
            {
                "opencloud": {
                    "domain": "cloud.test.example",
                    "data_dir": str(data_dir),
                    "config_dir": str(config_dir),
                    "apps_dir": str(apps_dir),
                },
                "weboffice": {"enabled": False},
            }
        )
    )

    for module in (backup_module, bundle_module):
        monkeypatch.setattr(module, "PROJECT_ROOT", project)
        monkeypatch.setattr(module, "STATE_DIR", state)
        monkeypatch.setattr(module, "DEPLOY_PATH", deploy)
        monkeypatch.setattr(module, "SECRETS_PATH", state / "secrets.yaml")

    bundle_path = tmp_path / "bundle.tar.gz"
    assert cmd_create(output=bundle_path, force=True) == 0

    (data_dir / "hello.txt").unlink()
    (state / "secrets.yaml").write_text("stale: true\n")

    assert cmd_restore(bundle_path, skip_apply=True) == 0
    assert (data_dir / "hello.txt").read_text() == "world\n"
    assert "INITIAL_ADMIN_PASSWORD" in (state / "secrets.yaml").read_text()
    assert yaml.safe_load(deploy.read_text())["opencloud"]["domain"] == "cloud.test.example"
