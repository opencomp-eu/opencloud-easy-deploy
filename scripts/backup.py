#!/usr/bin/env python3
"""Borg backup and restore for opencloud-easy-deploy."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = PROJECT_ROOT / ".opencloud-easy-deploy"
SECRETS_PATH = STATE_DIR / "secrets.yaml"
DEPLOY_PATH = PROJECT_ROOT / "deploy.yaml"
BACKUP_DIR = STATE_DIR / "backup"
BACKUP_COMPOSE = BACKUP_DIR / "docker-compose.yml"
BACKUP_RESTORE_COMPOSE = BACKUP_DIR / "docker-compose.restore.yml"
BACKUP_ENV = BACKUP_DIR / ".env"
BACKUP_SCRIPTS = BACKUP_DIR / "scripts"
BACKUP_RUN_SCRIPT = PROJECT_ROOT / "config-templates" / "backup" / "run-backup.sh"
SYSTEMD_DIR = BACKUP_DIR / "systemd"

BACKUP_ROOT = "/backup-root"
BORG_IMAGE = "borgbackup/borgbackup:1.4.5"


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def load_yaml(path: Path) -> dict:
    with path.open() as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be a mapping")
    return data


def load_config(path: Path | None = None) -> dict:
    path = path or DEPLOY_PATH
    if not path.exists():
        raise FileNotFoundError(f"Missing {path.name}. Run apply.sh first.")
    return load_yaml(path)


def backup_settings(config: dict) -> dict:
    backup = config.get("backup") or {}
    repository = backup.get("repository") or {}
    schedule = backup.get("schedule") or {}
    retention = backup.get("retention") or {}
    return {
        "enabled": to_bool(backup.get("enabled")),
        "repository_type": str(repository.get("type") or "local").lower(),
        "repository_path": str(repository.get("path") or "").strip(),
        "schedule_enabled": to_bool(schedule.get("enabled")),
        "schedule_calendar": str(schedule.get("calendar") or "*-*-* 03:00:00"),
        "schedule_persistent": to_bool(schedule.get("persistent", True)),
        "keep_daily": int(retention.get("keep_daily", 7)),
        "keep_weekly": int(retention.get("keep_weekly", 4)),
        "keep_monthly": int(retention.get("keep_monthly", 6)),
        "keep_yearly": int(retention.get("keep_yearly", 0)),
    }


def backup_enabled(config: dict) -> bool:
    return backup_settings(config)["enabled"]


def validate_backup_config(config: dict) -> None:
    settings = backup_settings(config)
    if not settings["enabled"]:
        return
    if settings["repository_type"] != "local":
        raise ValueError("backup.repository.type must be 'local' (only local repos are supported in v1)")
    repo_path = settings["repository_path"]
    if not repo_path:
        raise ValueError("backup.repository.path is required when backup is enabled")
    if not repo_path.startswith("/"):
        raise ValueError("backup.repository.path must be an absolute path")
    for key in ("keep_daily", "keep_weekly", "keep_monthly", "keep_yearly"):
        if settings[key] < 0:
            raise ValueError(f"backup.retention.{key} must be >= 0")


def backup_secret_keys(config: dict) -> tuple[str, ...]:
    if backup_enabled(config):
        return ("BORG_PASSPHRASE",)
    return ()


def backup_source_paths(config: dict) -> list[tuple[Path, str]]:
    """Host paths and in-container mount points under /backup-root/."""
    opencloud = config["opencloud"]
    data_root = Path(str(opencloud["data_dir"])).parent
    ldap_base = Path(str(opencloud["config_dir"])).parent

    mounts: list[tuple[Path, str]] = [
        (Path(str(opencloud["data_dir"])), f"{BACKUP_ROOT}/data"),
        (Path(str(opencloud["config_dir"])), f"{BACKUP_ROOT}/config"),
        (Path(str(opencloud["apps_dir"])), f"{BACKUP_ROOT}/apps"),
        (ldap_base / "ldap_certs", f"{BACKUP_ROOT}/ldap_certs"),
        (ldap_base / "ldap_data", f"{BACKUP_ROOT}/ldap_data"),
        (STATE_DIR / "secrets.yaml", f"{BACKUP_ROOT}/secrets/secrets.yaml"),
        (DEPLOY_PATH.resolve(), f"{BACKUP_ROOT}/secrets/deploy.yaml"),
    ]

    weboffice = config.get("weboffice") or {}
    if to_bool(weboffice.get("enabled")) and str(weboffice.get("type") or "") == "euro_office":
        mounts.append((data_root / "euro-office", f"{BACKUP_ROOT}/euro-office"))

    return mounts


def payload_arcname(container_path: str) -> str:
    """Map /backup-root/data → payload/data inside a portable bundle."""
    prefix = f"{BACKUP_ROOT.rstrip('/')}/"
    if not container_path.startswith(prefix):
        raise ValueError(f"unexpected backup mount path: {container_path}")
    return f"payload/{container_path[len(prefix):]}"


def backup_path_entries(config: dict) -> list[tuple[Path, str]]:
    """Host paths and their location inside a portable bundle archive."""
    return [(host, payload_arcname(container)) for host, container in backup_source_paths(config)]


def _compose_volumes(config: dict, *, read_only: bool) -> list[str]:
    settings = backup_settings(config)
    suffix = ":ro" if read_only else ""
    volumes = [f"{settings['repository_path']}:/repo"]
    for host_path, container_path in backup_source_paths(config):
        volumes.append(f"{host_path}:{container_path}{suffix}")
    volumes.append(f"{BACKUP_SCRIPTS.resolve()}:/scripts:ro")
    return volumes


def _write_compose(path: Path, config: dict, *, read_only: bool) -> None:
    settings = backup_settings(config)
    compose = {
        "services": {
            "borg": {
                "image": BORG_IMAGE,
                "container_name": "opencloud_borg",
                "network_mode": "none",
                "environment": {
                    "BORG_PASSPHRASE": "${BORG_PASSPHRASE}",
                    "BORG_REPO": "/repo",
                    "BORG_ARCHIVE_PREFIX": "${BORG_ARCHIVE_PREFIX}",
                    "KEEP_DAILY": "${KEEP_DAILY}",
                    "KEEP_WEEKLY": "${KEEP_WEEKLY}",
                    "KEEP_MONTHLY": "${KEEP_MONTHLY}",
                    "KEEP_YEARLY": "${KEEP_YEARLY}",
                },
                "volumes": _compose_volumes(config, read_only=read_only),
                "working_dir": "/",
                "entrypoint": ["/bin/sh"],
            }
        }
    }
    path.write_text(yaml.safe_dump(compose, sort_keys=False))


def write_backup_env(config: dict, secrets: dict[str, str]) -> None:
    settings = backup_settings(config)
    opencloud = config["opencloud"]
    domain = str(opencloud["domain"]).replace(".", "-")
    lines = [
        "# Generated by opencloud-easy-deploy — do not edit by hand.",
        "",
        f"BORG_PASSPHRASE={secrets['BORG_PASSPHRASE']}",
        f"BORG_REPO_PATH={settings['repository_path']}",
        f"BORG_ARCHIVE_PREFIX=opencloud-{domain}",
        f"KEEP_DAILY={settings['keep_daily']}",
        f"KEEP_WEEKLY={settings['keep_weekly']}",
        f"KEEP_MONTHLY={settings['keep_monthly']}",
        f"KEEP_YEARLY={settings['keep_yearly']}",
    ]
    BACKUP_ENV.parent.mkdir(parents=True, exist_ok=True)
    BACKUP_ENV.write_text("\n".join(lines) + "\n")
    os.chmod(BACKUP_ENV, 0o600)


def render_backup_compose(config: dict) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_SCRIPTS.mkdir(parents=True, exist_ok=True)
    _write_compose(BACKUP_COMPOSE, config, read_only=True)
    _write_compose(BACKUP_RESTORE_COMPOSE, config, read_only=False)
    shutil.copy2(BACKUP_RUN_SCRIPT, BACKUP_SCRIPTS / "run-backup.sh")
    os.chmod(BACKUP_SCRIPTS / "run-backup.sh", 0o755)


def render_systemd_units(config: dict) -> None:
    settings = backup_settings(config)
    SYSTEMD_DIR.mkdir(parents=True, exist_ok=True)
    backup_sh = (PROJECT_ROOT / "backup.sh").resolve()
    persistent = "true" if settings["schedule_persistent"] else "false"

    service = f"""[Unit]
Description=OpenCloud Borg backup
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=oneshot
WorkingDirectory={PROJECT_ROOT}
ExecStart={backup_sh}
"""
    timer = f"""[Unit]
Description=OpenCloud Borg backup schedule

[Timer]
OnCalendar={settings['schedule_calendar']}
Persistent={persistent}

[Install]
WantedBy=timers.target
"""
    (SYSTEMD_DIR / "opencloud-backup.service").write_text(service)
    (SYSTEMD_DIR / "opencloud-backup.timer").write_text(timer)


def bootstrap_backup(config: dict, secrets: dict[str, str]) -> None:
    if not backup_enabled(config):
        return
    repo_path = Path(backup_settings(config)["repository_path"])
    repo_path.mkdir(parents=True, exist_ok=True)
    os.chmod(repo_path, 0o700)
    render_backup_compose(config)
    write_backup_env(config, secrets)
    render_systemd_units(config)


def docker_compose_cmd() -> list[str]:
    if shutil.which("docker"):
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return ["docker", "compose"]
    compose = shutil.which("docker-compose")
    if compose:
        return [compose]
    raise RuntimeError("Docker Compose v2 is required (docker compose)")


def compose_run(
    shell_command: str,
    *,
    restore: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    compose_file = BACKUP_RESTORE_COMPOSE if restore else BACKUP_COMPOSE
    cmd = (
        docker_compose_cmd()
        + [
            "--env-file",
            str(BACKUP_ENV),
            "-f",
            str(compose_file),
            "run",
            "--rm",
            "borg",
            "-c",
            shell_command,
        ]
    )
    return subprocess.run(cmd, cwd=PROJECT_ROOT, check=check, text=True)


def cmd_run_backup() -> int:
    config = load_config()
    secrets = load_yaml(SECRETS_PATH)
    bootstrap_backup(config, secrets)
    result = compose_run("/scripts/run-backup.sh", check=False)
    return result.returncode


def cmd_list_archives() -> int:
    result = compose_run("borg list --short /repo", check=False)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode


def cmd_restore(*, archive: str | None, latest: bool, dry_run: bool, paths: list[str]) -> int:
    list_result = compose_run("borg list --short /repo", check=False)
    if list_result.returncode != 0:
        print(list_result.stderr or list_result.stdout, file=sys.stderr)
        return list_result.returncode

    archives = [line.strip() for line in list_result.stdout.splitlines() if line.strip()]
    if not archives:
        print("No archives found in repository.", file=sys.stderr)
        return 1

    if latest:
        archive_name = archives[-1]
    elif archive:
        archive_name = archive
        if archive_name not in archives:
            print(f"Archive not found: {archive_name}", file=sys.stderr)
            print("Available archives:", file=sys.stderr)
            for name in archives:
                print(f"  {name}", file=sys.stderr)
            return 1
    else:
        print("Available archives:")
        for name in archives:
            print(f"  {name}")
        return 0

    restore_paths = paths or ["backup-root"]

    if dry_run:
        cmd = "borg list /repo::" + shlex.quote(archive_name)
        if paths:
            cmd += " " + " ".join(shlex.quote(path) for path in restore_paths)
        print(f"Dry run — contents of {archive_name}:")
        result = compose_run(cmd, check=False)
        if result.stdout:
            print(result.stdout, end="")
        return result.returncode

    print(f"Restoring archive {archive_name}…")
    print("Ensure OpenCloud is stopped: bash stop.sh")
    extract_cmd = (
        f"borg extract --verbose /repo::{shlex.quote(archive_name)} "
        + " ".join(shlex.quote(path) for path in restore_paths)
    )
    result = compose_run(extract_cmd, restore=True, check=False)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode == 0:
        print()
        print("Restore complete. Next steps:")
        print("  1. Verify restored files under your data/config paths")
        print("  2. bash apply.sh")
        print("  3. bash start.sh")
    return result.returncode


def print_backup_summary(config: dict) -> None:
    if not backup_enabled(config):
        return
    settings = backup_settings(config)
    print()
    print("=== Backup (Borg) ===")
    print(f"Repository: {settings['repository_path']}")
    print(f"Passphrase:   {SECRETS_PATH} (key: BORG_PASSPHRASE)")
    print("Run now:      bash backup.sh")
    print("Portable:     bash backup-bundle.sh   # single .tar.gz for migration")
    print("List/restore: bash restore.sh --list")
    if settings["schedule_enabled"]:
        print("Schedule:     systemd timer files generated (install manually):")
        print(f"  sudo cp {SYSTEMD_DIR}/opencloud-backup.* /etc/systemd/system/")
        print("  sudo systemctl daemon-reload")
        print("  sudo systemctl enable --now opencloud-backup.timer")
    else:
        print("Schedule:     disabled — run bash backup.sh manually or set backup.schedule.enabled")


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenCloud Borg backup utilities")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="Create a backup archive now")
    sub.add_parser("list", help="List backup archives")

    restore_parser = sub.add_parser("restore", help="Restore from a backup archive")
    restore_parser.add_argument("--archive", help="Archive name to restore")
    restore_parser.add_argument("--latest", action="store_true", help="Restore newest archive")
    restore_parser.add_argument("--dry-run", action="store_true", help="List archive contents only")
    restore_parser.add_argument(
        "--path",
        action="append",
        dest="paths",
        help="Limit restore to a path inside the archive (repeatable)",
    )

    args = parser.parse_args()

    try:
        if args.command == "run":
            sys.exit(cmd_run_backup())
        if args.command == "list":
            sys.exit(cmd_list_archives())
        if args.command == "restore":
            sys.exit(
                cmd_restore(
                    archive=args.archive,
                    latest=args.latest,
                    dry_run=args.dry_run,
                    paths=args.paths or [],
                )
            )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
