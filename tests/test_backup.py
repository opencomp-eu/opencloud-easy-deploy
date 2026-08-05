"""Tests for scripts/backup.py."""

from __future__ import annotations

import pytest
import yaml

from scripts.backup import (
    backup_enabled,
    backup_settings,
    backup_source_paths,
    bootstrap_backup,
    render_backup_compose,
    validate_backup_config,
)


def _base_config(**overrides) -> dict:
    config = {
        "opencloud": {
            "domain": "cloud.test.example",
            "data_dir": "/var/lib/opencloud/data",
            "config_dir": "/var/lib/opencloud/config",
            "apps_dir": "/var/lib/opencloud/apps",
        },
        "weboffice": {
            "enabled": True,
            "type": "euro_office",
            "domain": "eurooffice.test.example",
        },
        "backup": {
            "enabled": False,
            "repository": {"type": "local", "path": "/var/backups/opencloud"},
            "schedule": {"enabled": False, "calendar": "*-*-* 03:00:00", "persistent": True},
            "retention": {
                "keep_daily": 7,
                "keep_weekly": 4,
                "keep_monthly": 6,
                "keep_yearly": 0,
            },
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and key in config and isinstance(config[key], dict):
            config[key].update(value)
        else:
            config[key] = value
    return config


def test_backup_disabled_by_default():
    assert not backup_enabled(_base_config())


def test_validate_backup_requires_absolute_path():
    config = _base_config(backup={"enabled": True, "repository": {"type": "local", "path": "relative"}})
    with pytest.raises(ValueError, match="absolute path"):
        validate_backup_config(config)


def test_validate_backup_rejects_unknown_repo_type():
    config = _base_config(
        backup={"enabled": True, "repository": {"type": "s3", "path": "/var/backups/opencloud"}}
    )
    with pytest.raises(ValueError, match="sftp"):
        validate_backup_config(config)


def test_borg_repo_url_local():
    from scripts.backup import borg_repo_url

    config = _base_config(backup={"enabled": True, "repository": {"type": "local", "path": "/var/backups/opencloud"}})
    assert borg_repo_url(config) == "/repo"


def test_borg_repo_url_sftp():
    from scripts.backup import borg_repo_url

    config = _base_config(
        backup={
            "enabled": True,
            "repository": {
                "type": "sftp",
                "host": "backup.example.com",
                "user": "borg",
                "path": "/repos/opencloud",
                "port": 2222,
                "ssh_key_path": "/root/.ssh/key",
            },
        }
    )
    assert borg_repo_url(config) == "ssh://borg@backup.example.com:2222/repos/opencloud"


def test_validate_sftp_requires_ssh_key(tmp_path):
    config = _base_config(
        backup={
            "enabled": True,
            "repository": {
                "type": "sftp",
                "host": "backup.example.com",
                "user": "borg",
                "path": "/repos/opencloud",
                "ssh_key_path": str(tmp_path / "missing"),
            },
        }
    )
    with pytest.raises(ValueError, match="ssh_key_path not found"):
        validate_backup_config(config)


def test_render_sftp_compose_mounts_ssh_key(tmp_path, monkeypatch):
    from scripts import backup as backup_module

    ssh_key = tmp_path / "borg_key"
    ssh_key.write_text("fake-key\n")
    backup_dir = tmp_path / "backup"
    monkeypatch.setattr(backup_module, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(backup_module, "BACKUP_COMPOSE", backup_dir / "docker-compose.yml")
    monkeypatch.setattr(
        backup_module, "BACKUP_RESTORE_COMPOSE", backup_dir / "docker-compose.restore.yml"
    )
    monkeypatch.setattr(backup_module, "BACKUP_SCRIPTS", backup_dir / "scripts")
    monkeypatch.setattr(backup_module, "BACKUP_RUN_SCRIPT", tmp_path / "run-backup.sh")
    (tmp_path / "run-backup.sh").write_text("#!/bin/sh\n")
    monkeypatch.setattr(backup_module, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(backup_module, "DEPLOY_PATH", tmp_path / "deploy.yaml")
    (tmp_path / "deploy.yaml").write_text("opencloud: {}\n")

    config = _base_config(
        backup={
            "enabled": True,
            "repository": {
                "type": "sftp",
                "host": "backup.example.com",
                "user": "borg",
                "path": "/repos/opencloud",
                "ssh_key_path": str(ssh_key),
            },
        }
    )
    render_backup_compose(config)

    compose = yaml.safe_load((backup_dir / "docker-compose.yml").read_text())
    service = compose["services"]["borg"]
    assert "network_mode" not in service
    assert any(str(ssh_key) in vol for vol in service["volumes"])
    assert service["environment"]["BORG_RSH"] == "${BORG_RSH}"


def test_bootstrap_writes_sftp_env(tmp_path, monkeypatch):
    from scripts import backup as backup_module

    ssh_key = tmp_path / "borg_key"
    ssh_key.write_text("fake-key\n")
    data_root = tmp_path / "opencloud"
    backup_dir = tmp_path / "backup"
    monkeypatch.setattr(backup_module, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(backup_module, "BACKUP_COMPOSE", backup_dir / "docker-compose.yml")
    monkeypatch.setattr(
        backup_module, "BACKUP_RESTORE_COMPOSE", backup_dir / "docker-compose.restore.yml"
    )
    monkeypatch.setattr(backup_module, "BACKUP_ENV", backup_dir / ".env")
    monkeypatch.setattr(backup_module, "BACKUP_SCRIPTS", backup_dir / "scripts")
    monkeypatch.setattr(backup_module, "BACKUP_RUN_SCRIPT", tmp_path / "run-backup.sh")
    monkeypatch.setattr(backup_module, "SYSTEMD_DIR", backup_dir / "systemd")
    (tmp_path / "run-backup.sh").write_text("#!/bin/sh\n")
    monkeypatch.setattr(backup_module, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(backup_module, "DEPLOY_PATH", tmp_path / "deploy.yaml")
    (tmp_path / "deploy.yaml").write_text("opencloud: {}\n")

    config = _base_config(
        opencloud={
            "data_dir": str(data_root / "data"),
            "config_dir": str(data_root / "config"),
            "apps_dir": str(data_root / "apps"),
        },
        backup={
            "enabled": True,
            "repository": {
                "type": "sftp",
                "host": "backup.example.com",
                "user": "borg",
                "path": "/repos/opencloud",
                "ssh_key_path": str(ssh_key),
            },
        },
    )
    bootstrap_backup(config, {"BORG_PASSPHRASE": "test-passphrase"})
    env_text = (backup_dir / ".env").read_text()
    assert "BORG_REPO=ssh://borg@backup.example.com/repos/opencloud" in env_text
    assert "BORG_RSH=" in env_text


def test_bootstrap_config_from_env_sftp(monkeypatch):
    from scripts.backup import bootstrap_config_from_env

    monkeypatch.setenv("OCD_BACKUP_SFTP_HOST", "backup.example.com")
    monkeypatch.setenv("OCD_BACKUP_SFTP_USER", "borg")
    monkeypatch.setenv("OCD_BACKUP_SFTP_PATH", "/repos/opencloud")
    monkeypatch.setenv("OCD_BACKUP_SSH_KEY", "/root/.ssh/key")
    config = bootstrap_config_from_env()
    assert config is not None
    assert config["backup"]["repository"]["type"] == "sftp"


def test_bootstrap_config_from_env_local(monkeypatch):
    from scripts.backup import bootstrap_config_from_env

    monkeypatch.setenv("OCD_BACKUP_LOCAL_PATH", "/var/backups/opencloud")
    config = bootstrap_config_from_env()
    assert config is not None
    assert config["backup"]["repository"]["type"] == "local"


def test_fresh_restore_needs_secrets_phase(tmp_path, monkeypatch):
    from scripts import backup as backup_module

    monkeypatch.setattr(backup_module, "DEPLOY_PATH", tmp_path / "deploy.yaml")
    assert backup_module._fresh_restore_needs_secrets_phase() is True

    (tmp_path / "deploy.yaml").write_text(
        yaml.safe_dump(
            {
                "opencloud": {"domain": "x.example"},
                "backup": {"enabled": True, "repository": {"type": "local", "path": "/repo"}},
            }
        )
    )
    assert backup_module._fresh_restore_needs_secrets_phase() is False


def test_backup_source_paths_include_euro_office(tmp_path, monkeypatch):
    from scripts import backup as backup_module

    monkeypatch.setattr(backup_module, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(backup_module, "DEPLOY_PATH", tmp_path / "deploy.yaml")
    (tmp_path / "deploy.yaml").write_text("opencloud: {}\n")

    config = _base_config()
    paths = {container: host for host, container in backup_source_paths(config)}
    assert str(paths["/backup-root/data"]).endswith("/var/lib/opencloud/data")
    assert str(paths["/backup-root/euro-office"]).endswith("/var/lib/opencloud/euro-office")
    assert "/backup-root/secrets/secrets.yaml" in paths


def test_render_backup_compose_creates_ro_and_rw_files(tmp_path, monkeypatch):
    from scripts import backup as backup_module

    backup_dir = tmp_path / "backup"
    monkeypatch.setattr(backup_module, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(backup_module, "BACKUP_COMPOSE", backup_dir / "docker-compose.yml")
    monkeypatch.setattr(
        backup_module, "BACKUP_RESTORE_COMPOSE", backup_dir / "docker-compose.restore.yml"
    )
    monkeypatch.setattr(backup_module, "BACKUP_SCRIPTS", backup_dir / "scripts")
    monkeypatch.setattr(backup_module, "BACKUP_RUN_SCRIPT", tmp_path / "run-backup.sh")
    (tmp_path / "run-backup.sh").write_text("#!/bin/sh\n")
    monkeypatch.setattr(backup_module, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(backup_module, "DEPLOY_PATH", tmp_path / "deploy.yaml")
    (tmp_path / "deploy.yaml").write_text("opencloud: {}\n")

    config = _base_config(backup={"enabled": True})
    render_backup_compose(config)

    backup = yaml.safe_load((backup_dir / "docker-compose.yml").read_text())
    restore = yaml.safe_load((backup_dir / "docker-compose.restore.yml").read_text())
    backup_vols = backup["services"]["borg"]["volumes"]
    restore_vols = restore["services"]["borg"]["volumes"]

    assert any(v.endswith(":ro") for v in backup_vols if "/backup-root/" in v)
    assert not any(v.endswith(":ro") for v in restore_vols if "/backup-root/" in v)


def test_bootstrap_backup_creates_repo_dir(tmp_path, monkeypatch):
    from scripts import backup as backup_module

    repo = tmp_path / "repo"
    data_root = tmp_path / "opencloud"
    backup_dir = tmp_path / "backup"
    monkeypatch.setattr(backup_module, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(backup_module, "BACKUP_COMPOSE", backup_dir / "docker-compose.yml")
    monkeypatch.setattr(
        backup_module, "BACKUP_RESTORE_COMPOSE", backup_dir / "docker-compose.restore.yml"
    )
    monkeypatch.setattr(backup_module, "BACKUP_ENV", backup_dir / ".env")
    monkeypatch.setattr(backup_module, "BACKUP_SCRIPTS", backup_dir / "scripts")
    monkeypatch.setattr(backup_module, "BACKUP_RUN_SCRIPT", tmp_path / "run-backup.sh")
    monkeypatch.setattr(backup_module, "SYSTEMD_DIR", backup_dir / "systemd")
    (tmp_path / "run-backup.sh").write_text("#!/bin/sh\n")
    monkeypatch.setattr(backup_module, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(backup_module, "DEPLOY_PATH", tmp_path / "deploy.yaml")
    (tmp_path / "deploy.yaml").write_text("opencloud: {}\n")

    config = _base_config(
        opencloud={
            "data_dir": str(data_root / "data"),
            "config_dir": str(data_root / "config"),
            "apps_dir": str(data_root / "apps"),
        },
        backup={
            "enabled": True,
            "repository": {"type": "local", "path": str(repo)},
        },
    )
    bootstrap_backup(config, {"BORG_PASSPHRASE": "test-passphrase"})
    assert repo.is_dir()
    assert (backup_dir / ".env").read_text().startswith("# Generated")
    assert "BORG_PASSPHRASE=test-passphrase" in (backup_dir / ".env").read_text()
