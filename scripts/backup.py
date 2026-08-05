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
BORG_SSH_KEY_MOUNT = "/root/.ssh/borg_key"

BACKUP_ROOT = "/backup-root"
BORG_IMAGE = "borgbackup/borgbackup:1.4.5"
ARCHIVE_PAYLOAD_ROOT = "backup-root"


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


def repository_settings(config: dict) -> dict:
    repository = (config.get("backup") or {}).get("repository") or {}
    repo_type = str(repository.get("type") or "local").lower()
    port_raw = repository.get("port")
    port = int(port_raw) if port_raw not in (None, "") else 22
    return {
        "type": repo_type,
        "path": str(repository.get("path") or "").strip(),
        "host": str(repository.get("host") or "").strip(),
        "user": str(repository.get("user") or "").strip(),
        "port": port,
        "ssh_key_path": str(repository.get("ssh_key_path") or "").strip(),
        "host_key_check": to_bool(repository.get("host_key_check", True)),
    }


def backup_settings(config: dict) -> dict:
    backup = config.get("backup") or {}
    repository = repository_settings(config)
    schedule = backup.get("schedule") or {}
    retention = backup.get("retention") or {}
    return {
        "enabled": to_bool(backup.get("enabled")),
        "repository_type": repository["type"],
        "repository_path": repository["path"],
        "repository_host": repository["host"],
        "repository_user": repository["user"],
        "repository_port": repository["port"],
        "ssh_key_path": repository["ssh_key_path"],
        "host_key_check": repository["host_key_check"],
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


def borg_repo_url(config: dict) -> str:
    repo = repository_settings(config)
    if repo["type"] == "local":
        return "/repo"
    if repo["type"] != "sftp":
        raise ValueError(f"unsupported repository type: {repo['type']}")
    remote_path = repo["path"].lstrip("/")
    port_suffix = f":{repo['port']}" if repo["port"] != 22 else ""
    return f"ssh://{repo['user']}@{repo['host']}{port_suffix}/{remote_path}"


def borg_rsh(config: dict) -> str:
    repo = repository_settings(config)
    if repo["type"] != "sftp":
        return ""
    if not repo["ssh_key_path"]:
        raise ValueError("backup.repository.ssh_key_path is required for sftp")
    key_path = Path(repo["ssh_key_path"])
    if not key_path.is_file():
        raise FileNotFoundError(f"SSH key not found: {key_path}")
    host_check = (
        "-o StrictHostKeyChecking=accept-new"
        if repo["host_key_check"]
        else "-o StrictHostKeyChecking=no"
    )
    port_opt = f"-p {repo['port']}" if repo["port"] != 22 else ""
    parts = ["ssh", "-i", BORG_SSH_KEY_MOUNT, host_check]
    if port_opt:
        parts.append(port_opt)
    return " ".join(parts)


def validate_backup_config(config: dict) -> None:
    settings = backup_settings(config)
    if not settings["enabled"]:
        return
    repo_type = settings["repository_type"]
    if repo_type not in {"local", "sftp"}:
        raise ValueError("backup.repository.type must be 'local' or 'sftp'")
    if not settings["repository_path"]:
        raise ValueError("backup.repository.path is required when backup is enabled")
    if repo_type == "local":
        if not settings["repository_path"].startswith("/"):
            raise ValueError("backup.repository.path must be an absolute path for local repos")
    else:
        if not settings["repository_host"]:
            raise ValueError("backup.repository.host is required for sftp")
        if not settings["repository_user"]:
            raise ValueError("backup.repository.user is required for sftp")
        if not settings["ssh_key_path"]:
            raise ValueError("backup.repository.ssh_key_path is required for sftp")
        if not Path(settings["ssh_key_path"]).is_file():
            raise ValueError(f"backup.repository.ssh_key_path not found: {settings['ssh_key_path']}")
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


def secrets_bootstrap_paths() -> list[tuple[Path, str]]:
    """Minimal mounts to restore deploy.yaml and secrets.yaml from Borg."""
    return [
        (STATE_DIR / "secrets.yaml", f"{BACKUP_ROOT}/secrets/secrets.yaml"),
        (DEPLOY_PATH.resolve(), f"{BACKUP_ROOT}/secrets/deploy.yaml"),
    ]


def payload_arcname(container_path: str) -> str:
    prefix = f"{BACKUP_ROOT.rstrip('/')}/"
    if not container_path.startswith(prefix):
        raise ValueError(f"unexpected backup mount path: {container_path}")
    return f"payload/{container_path[len(prefix):]}"


def backup_path_entries(config: dict) -> list[tuple[Path, str]]:
    return [(host, payload_arcname(container)) for host, container in backup_source_paths(config)]


def _compose_volumes(
    config: dict,
    *,
    read_only: bool,
    path_mounts: list[tuple[Path, str]] | None = None,
) -> list[str]:
    repo = repository_settings(config)
    suffix = ":ro" if read_only else ""
    volumes: list[str] = []
    if repo["type"] == "local":
        volumes.append(f"{repo['path']}:/repo")
    else:
        volumes.append(f"{repo['ssh_key_path']}:{BORG_SSH_KEY_MOUNT}:ro")
    for host_path, container_path in path_mounts or backup_source_paths(config):
        volumes.append(f"{host_path}:{container_path}{suffix}")
    volumes.append(f"{BACKUP_SCRIPTS.resolve()}:/scripts:ro")
    return volumes


def _write_compose(
    path: Path,
    config: dict,
    *,
    read_only: bool,
    path_mounts: list[tuple[Path, str]] | None = None,
) -> None:
    repo = repository_settings(config)
    environment = {
        "BORG_PASSPHRASE": "${BORG_PASSPHRASE}",
        "BORG_REPO": "${BORG_REPO}",
        "BORG_ARCHIVE_PREFIX": "${BORG_ARCHIVE_PREFIX}",
        "KEEP_DAILY": "${KEEP_DAILY}",
        "KEEP_WEEKLY": "${KEEP_WEEKLY}",
        "KEEP_MONTHLY": "${KEEP_MONTHLY}",
        "KEEP_YEARLY": "${KEEP_YEARLY}",
    }
    if repo["type"] == "sftp":
        environment["BORG_RSH"] = "${BORG_RSH}"

    service: dict[str, Any] = {
        "image": BORG_IMAGE,
        "container_name": "opencloud_borg",
        "environment": environment,
        "volumes": _compose_volumes(config, read_only=read_only, path_mounts=path_mounts),
        "working_dir": "/",
        "entrypoint": ["/bin/sh"],
    }
    if repo["type"] == "local":
        service["network_mode"] = "none"

    compose = {"services": {"borg": service}}
    path.write_text(yaml.safe_dump(compose, sort_keys=False))


def write_backup_env(config: dict, secrets: dict[str, str]) -> None:
    settings = backup_settings(config)
    opencloud = config["opencloud"]
    domain = str(opencloud["domain"]).replace(".", "-")
    lines = [
        "# Generated by opencloud-easy-deploy — do not edit by hand.",
        "",
        f"BORG_PASSPHRASE={secrets['BORG_PASSPHRASE']}",
        f"BORG_REPO={borg_repo_url(config)}",
        f"BORG_ARCHIVE_PREFIX=opencloud-{domain}",
        f"KEEP_DAILY={settings['keep_daily']}",
        f"KEEP_WEEKLY={settings['keep_weekly']}",
        f"KEEP_MONTHLY={settings['keep_monthly']}",
        f"KEEP_YEARLY={settings['keep_yearly']}",
    ]
    if settings["repository_type"] == "local":
        lines.append(f"BORG_REPO_PATH={settings['repository_path']}")
    rsh = borg_rsh(config)
    if rsh:
        lines.append(f"BORG_RSH={shlex.quote(rsh)}")
    BACKUP_ENV.parent.mkdir(parents=True, exist_ok=True)
    BACKUP_ENV.write_text("\n".join(lines) + "\n")
    os.chmod(BACKUP_ENV, 0o600)


def ensure_secrets_paths_exist() -> None:
    for host_path, _ in secrets_bootstrap_paths():
        host_path.parent.mkdir(parents=True, exist_ok=True)
        if host_path.suffix == ".yaml" and not host_path.exists():
            host_path.touch()


def ensure_backup_paths_exist(config: dict) -> None:
    for host_path, _ in backup_source_paths(config):
        if host_path.suffix == ".yaml":
            host_path.parent.mkdir(parents=True, exist_ok=True)
            if not host_path.exists():
                host_path.touch()
        else:
            host_path.mkdir(parents=True, exist_ok=True)


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


def bootstrap_secrets_only_restore(config: dict, secrets: dict[str, str]) -> None:
    """Phase 1: connect to Borg; mount only paths needed for deploy.yaml + secrets.yaml."""
    repo = repository_settings(config)
    if repo["type"] == "sftp":
        if not Path(repo["ssh_key_path"]).is_file():
            raise FileNotFoundError(f"SSH key not found: {repo['ssh_key_path']}")
    elif repo["type"] == "local" and not Path(repo["path"]).is_dir():
        raise FileNotFoundError(f"Local Borg repository not found: {repo['path']}")

    ensure_secrets_paths_exist()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_SCRIPTS.mkdir(parents=True, exist_ok=True)
    _write_compose(
        BACKUP_RESTORE_COMPOSE,
        config,
        read_only=False,
        path_mounts=secrets_bootstrap_paths(),
    )
    write_backup_env(config, secrets)


def bootstrap_backup(config: dict, secrets: dict[str, str]) -> None:
    if not backup_enabled(config):
        return
    repo = repository_settings(config)
    if repo["type"] == "local":
        repo_path = Path(repo["path"])
        repo_path.mkdir(parents=True, exist_ok=True)
        os.chmod(repo_path, 0o700)
    ensure_backup_paths_exist(config)
    render_backup_compose(config)
    write_backup_env(config, secrets)
    render_systemd_units(config)


def bootstrap_config_from_env() -> dict | None:
    """Build minimal config from OCD_BACKUP_* env vars (fresh VPS, before deploy.yaml exists)."""
    local_path = os.environ.get("OCD_BACKUP_LOCAL_PATH", "").strip()
    if local_path:
        return {
            "opencloud": {"domain": "bootstrap.local"},
            "backup": {
                "enabled": True,
                "repository": {"type": "local", "path": local_path},
            },
        }

    host = os.environ.get("OCD_BACKUP_SFTP_HOST", "").strip()
    user = os.environ.get("OCD_BACKUP_SFTP_USER", "").strip()
    path = os.environ.get("OCD_BACKUP_SFTP_PATH", "").strip()
    ssh_key = os.environ.get("OCD_BACKUP_SSH_KEY", "").strip()
    if not all((host, user, path, ssh_key)):
        return None
    port = int(os.environ.get("OCD_BACKUP_SFTP_PORT", "22") or "22")
    return {
        "opencloud": {"domain": "bootstrap.local"},
        "backup": {
            "enabled": True,
            "repository": {
                "type": "sftp",
                "host": host,
                "user": user,
                "path": path,
                "port": port,
                "ssh_key_path": ssh_key,
                "host_key_check": to_bool(os.environ.get("OCD_BACKUP_HOST_KEY_CHECK", "true")),
            },
        },
    }


def resolve_connection_config() -> dict:
    """Repository connection: deploy.yaml on disk, or OCD_BACKUP_* env vars."""
    if DEPLOY_PATH.is_file():
        config = load_yaml(DEPLOY_PATH)
        if backup_enabled(config):
            return config
    env_config = bootstrap_config_from_env()
    if env_config:
        return env_config
    raise FileNotFoundError(
        "Set OCD_BACKUP_SFTP_* (or OCD_BACKUP_LOCAL_PATH), or place deploy.yaml here first"
    )


def _fresh_restore_needs_secrets_phase() -> bool:
    if not DEPLOY_PATH.is_file():
        return True
    try:
        return not backup_enabled(load_yaml(DEPLOY_PATH))
    except ValueError:
        return True


def _run_borg_extract(archive_name: str, paths: list[str]) -> subprocess.CompletedProcess[str]:
    extract_cmd = (
        f"borg extract --verbose {_repo_shell_var()}::{shlex.quote(archive_name)} "
        + " ".join(shlex.quote(path) for path in paths)
    )
    return compose_run(extract_cmd, restore=True, check=False)


def _prepare_passphrase_secrets(passphrase: str) -> dict[str, str]:
    secrets: dict[str, str] = {"BORG_PASSPHRASE": passphrase}
    if SECRETS_PATH.is_file():
        existing = load_yaml(SECRETS_PATH)
        existing.update(secrets)
        secrets = existing
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with SECRETS_PATH.open("w") as handle:
        yaml.safe_dump(secrets, handle, default_flow_style=False)
    os.chmod(SECRETS_PATH, 0o600)
    return secrets


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


def _repo_shell_var() -> str:
    return "${BORG_REPO}"


def _list_archives() -> list[str]:
    result = compose_run(f"borg list --short {_repo_shell_var()}", check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "borg list failed")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _resolve_archive_name(archive: str | None, latest: bool) -> str | None:
    archives = _list_archives()
    if not archives:
        print("No archives found in repository.", file=sys.stderr)
        return None
    if latest:
        return archives[-1]
    if archive:
        if archive not in archives:
            print(f"Archive not found: {archive}", file=sys.stderr)
            print("Available archives:", file=sys.stderr)
            for name in archives:
                print(f"  {name}", file=sys.stderr)
            return None
        return archive
    print("Available archives:")
    for name in archives:
        print(f"  {name}")
    return None


def cmd_run_backup() -> int:
    config = load_config()
    secrets = load_yaml(SECRETS_PATH)
    bootstrap_backup(config, secrets)
    result = compose_run("/scripts/run-backup.sh", check=False)
    return result.returncode


def cmd_list_archives() -> int:
    if not BACKUP_ENV.is_file():
        config = resolve_connection_config()
        passphrase = os.environ.get("BORG_PASSPHRASE", "").strip()
        if not passphrase:
            raise ValueError("Set BORG_PASSPHRASE to access the repository")
        bootstrap_secrets_only_restore(config, {"BORG_PASSPHRASE": passphrase})
    result = compose_run(f"borg list --short {_repo_shell_var()}", check=False)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode


def cmd_restore(*, archive: str | None, latest: bool, dry_run: bool, paths: list[str]) -> int:
    if not BACKUP_ENV.is_file():
        bootstrap_backup(load_config(), load_yaml(SECRETS_PATH))

    archive_name = _resolve_archive_name(archive, latest)
    if archive_name is None:
        return 1 if archive or latest else 0

    restore_paths = paths or [ARCHIVE_PAYLOAD_ROOT]

    if dry_run:
        cmd = f"borg list {_repo_shell_var()}::{shlex.quote(archive_name)}"
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
        f"borg extract --verbose {_repo_shell_var()}::{shlex.quote(archive_name)} "
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
    return result.returncode


def cmd_fresh_restore(*, archive: str | None, latest: bool) -> int:
    passphrase = os.environ.get("BORG_PASSPHRASE", "").strip()
    if not passphrase:
        raise ValueError("Set BORG_PASSPHRASE to restore from Borg (keep it in your password manager)")

    secrets = _prepare_passphrase_secrets(passphrase)
    needs_config_from_archive = _fresh_restore_needs_secrets_phase()

    if needs_config_from_archive:
        conn = bootstrap_config_from_env()
        if not conn:
            raise ValueError(
                "On a fresh VPS set OCD_BACKUP_SFTP_* or OCD_BACKUP_LOCAL_PATH "
                "so we can reach your Borg repository"
            )
        validate_backup_config({**conn, "backup": {**conn["backup"], "enabled": True}})
        bootstrap_secrets_only_restore(conn, secrets)
    elif not BACKUP_ENV.is_file():
        bootstrap_backup(load_config(), secrets)

    archive_name = _resolve_archive_name(archive, latest=latest or not archive)
    if archive_name is None:
        return 1

    if needs_config_from_archive:
        print(f"Fetching deploy.yaml from archive {archive_name}…")
        result = _run_borg_extract(archive_name, [f"{ARCHIVE_PAYLOAD_ROOT}/secrets"])
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.returncode != 0:
            return result.returncode
        if not DEPLOY_PATH.is_file():
            raise RuntimeError("Archive did not contain backup-root/secrets/deploy.yaml")

    config = load_config()
    validate_backup_config(config)
    secrets = load_yaml(SECRETS_PATH) if SECRETS_PATH.is_file() else secrets
    secrets["BORG_PASSPHRASE"] = passphrase

    print(f"Restoring archive {archive_name}…")
    bootstrap_backup(config, secrets)
    result = _run_borg_extract(archive_name, [ARCHIVE_PAYLOAD_ROOT])
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        return result.returncode

    os.chmod(SECRETS_PATH, 0o600)
    print()
    print("Borg restore complete. deploy.yaml and secrets.yaml came from the archive.")
    print("Next: bash apply.sh (restore-borg.sh runs this automatically)")
    return 0


def print_backup_summary(config: dict) -> None:
    if not backup_enabled(config):
        return
    settings = backup_settings(config)
    print()
    print("=== Backup (Borg) ===")
    if settings["repository_type"] == "sftp":
        print(
            f"Repository: sftp://{settings['repository_user']}@{settings['repository_host']}{settings['repository_path']}"
        )
    else:
        print(f"Repository: {settings['repository_path']}")
    print(f"Passphrase:   {SECRETS_PATH} (key: BORG_PASSPHRASE) — keep safe off-site")
    print("Run now:      bash backup.sh")
    print("Portable:     bash backup-bundle.sh")
    print("Fresh VPS:    bash restore-borg.sh  (passphrase + SFTP or local repo path)")
    print("List/restore: bash restore.sh --list")
    if settings["schedule_enabled"]:
        print("Schedule:     systemd timer — see .opencloud-easy-deploy/backup/systemd/")
    else:
        print("Schedule:     disabled")


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

    fresh_parser = sub.add_parser("fresh-restore", help="Restore on empty host from Borg remote/local")
    fresh_parser.add_argument("--archive", help="Archive name (default: latest)")
    fresh_parser.add_argument("--latest", action="store_true", help="Restore newest archive")

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
        if args.command == "fresh-restore":
            sys.exit(cmd_fresh_restore(archive=args.archive, latest=args.latest))
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
