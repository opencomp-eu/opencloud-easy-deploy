#!/usr/bin/env python3
"""Portable backup bundles — one archive for VPS migration."""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import yaml

from scripts.backup import (
    BACKUP_ROOT,
    DEPLOY_PATH,
    PROJECT_ROOT,
    SECRETS_PATH,
    STATE_DIR,
    backup_path_entries,
    load_config,
    load_yaml,
)

BUNDLE_VERSION = 1
MANIFEST_NAME = "MANIFEST.yaml"
OPENCLOUD_UID = 1000
OPENCLOUD_GID = 1000


def _utc_timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _default_output_name(config: dict) -> str:
    domain = str(config["opencloud"]["domain"]).replace(".", "-")
    return f"opencloud-backup-{domain}-{_utc_timestamp()}.tar.gz"


def _build_manifest(config: dict, entries: list[tuple[Path, str]]) -> dict[str, Any]:
    return {
        "bundle_version": BUNDLE_VERSION,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "hostname": socket.gethostname(),
        "created_by": getpass.getuser(),
        "opencloud_domain": config["opencloud"]["domain"],
        "entries": [
            {"host_path": str(host), "bundle_path": bundle_path}
            for host, bundle_path in entries
        ],
    }


def _copy_tree_or_file(source: Path, destination: Path) -> None:
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return
    if source.is_dir():
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
        return
    raise FileNotFoundError(f"backup source missing: {source}")


def _staging_add(staging: Path, source: Path, bundle_path: str) -> None:
    _copy_tree_or_file(source, staging / bundle_path)


def _tar_compression_mode(output: Path) -> str:
    suffix = output.name.lower()
    if suffix.endswith((".tar.gz", ".tgz")):
        return "w:gz"
    if suffix.endswith(".tar.zst"):
        if shutil.which("zstd") is None:
            raise RuntimeError(".tar.zst requires the zstd command — use .tar.gz instead")
        return "w|zst"
    if suffix.endswith(".tar"):
        return "w"
    raise ValueError("bundle output must end with .tar.gz, .tgz, .tar.zst, or .tar")


def _write_archive(staging: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    mode = _tar_compression_mode(output)
    if mode == "w|zst":
        raw_tar = output.with_suffix("")  # drop .zst only — keep .tar
        with tarfile.open(raw_tar, "w") as tar:
            for item in sorted(staging.iterdir()):
                tar.add(item, arcname=item.name)
        subprocess.run(
            ["zstd", "-fq", "-19", str(raw_tar), "-o", str(output)],
            check=True,
        )
        raw_tar.unlink()
        return

    with tarfile.open(output, mode) as tar:
        for item in sorted(staging.iterdir()):
            tar.add(item, arcname=item.name)


def _read_archive(archive: Path, destination: Path) -> None:
    name = archive.name.lower()
    if name.endswith(".tar.zst"):
        if shutil.which("zstd") is None:
            raise RuntimeError(".tar.zst requires the zstd command")
        raw_tar = destination / "bundle.tar"
        subprocess.run(["zstd", "-dq", str(archive), "-o", str(raw_tar)], check=True)
        with tarfile.open(raw_tar, "r") as tar:
            tar.extractall(path=destination, filter="data")
        raw_tar.unlink()
        return

    mode = "r:gz" if name.endswith((".tar.gz", ".tgz")) else "r"
    with tarfile.open(archive, mode) as tar:
        tar.extractall(path=destination, filter="data")


def _opencloud_running() -> bool:
    return subprocess.run(["docker", "inspect", "opencloud"], capture_output=True).returncode == 0


def cmd_create(*, output: Path | None, force: bool) -> int:
    config = load_config()
    if not DEPLOY_PATH.is_file():
        raise FileNotFoundError(f"Missing {DEPLOY_PATH}")
    if not SECRETS_PATH.is_file():
        raise FileNotFoundError(f"Missing {SECRETS_PATH} — run apply.sh first")

    if _opencloud_running() and not force:
        print(
            "Warning: OpenCloud is running — stop first for a consistent backup:",
            file=sys.stderr,
        )
        print("  bash stop.sh && bash backup-bundle.sh", file=sys.stderr)
        print("Or pass --force to continue anyway.", file=sys.stderr)
        return 1

    entries = backup_path_entries(config)
    missing = [str(host) for host, _ in entries if not host.exists()]
    if missing:
        print("Warning: some backup paths do not exist and will be skipped:", file=sys.stderr)
        for path in missing:
            print(f"  - {path}", file=sys.stderr)

    output = (output or Path(_default_output_name(config))).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing bundle: {output}")

    with tempfile.TemporaryDirectory(prefix="ocd-bundle-") as tmp:
        staging = Path(tmp)
        manifest = _build_manifest(config, entries)
        (staging / MANIFEST_NAME).write_text(yaml.safe_dump(manifest, sort_keys=False))
        shutil.copy2(DEPLOY_PATH, staging / "deploy.yaml")
        shutil.copy2(SECRETS_PATH, staging / "secrets.yaml")

        for host, bundle_path in entries:
            if host.exists():
                _staging_add(staging, host, bundle_path)

        _write_archive(staging, output)

    size_mb = output.stat().st_size / (1024 * 1024)
    print(f"Created portable backup: {output} ({size_mb:.1f} MiB)")
    print()
    print("Migration — on a fresh VPS:")
    print("  git clone <repo> opencloud-easy-deploy && cd opencloud-easy-deploy")
    print(f"  bash restore-bundle.sh {output}")
    print()
    print("This file contains secrets — store and transfer it securely.")
    return 0


def _install_with_sudo(source: Path, destination: Path) -> None:
    destination = destination.resolve()
    subprocess.run(["sudo", "mkdir", "-p", str(destination.parent)], check=True)
    if destination.exists() or destination.is_symlink():
        if destination.is_dir() and not destination.is_symlink():
            subprocess.run(["sudo", "rm", "-rf", str(destination)], check=True)
        else:
            subprocess.run(["sudo", "rm", "-f", str(destination)], check=True)
    subprocess.run(["sudo", "cp", "-a", str(source), str(destination)], check=True)


def _install_entry(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        _copy_tree_or_file(source, destination)
    except PermissionError:
        _install_with_sudo(source, destination)


def _maybe_fix_opencloud_ownership(host_path: Path) -> None:
    if os.geteuid() != 0:
        return
    path = str(host_path)
    if not path.startswith("/var/lib/opencloud"):
        return
    subprocess.run(
        ["chown", "-R", f"{OPENCLOUD_UID}:{OPENCLOUD_GID}", path],
        check=False,
    )


def cmd_restore(archive: Path, *, skip_apply: bool) -> int:
    archive = archive.resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"Bundle not found: {archive}")

    with tempfile.TemporaryDirectory(prefix="ocd-restore-") as tmp:
        extracted = Path(tmp)
        _read_archive(archive, extracted)

        manifest_path = extracted / MANIFEST_NAME
        if not manifest_path.is_file():
            raise ValueError(f"Invalid bundle: missing {MANIFEST_NAME}")

        manifest = load_yaml(manifest_path)
        if manifest.get("bundle_version") != BUNDLE_VERSION:
            raise ValueError(
                f"Unsupported bundle version: {manifest.get('bundle_version')!r}"
            )

        deploy_src = extracted / "deploy.yaml"
        secrets_src = extracted / "secrets.yaml"
        if not deploy_src.is_file() or not secrets_src.is_file():
            raise ValueError("Invalid bundle: missing deploy.yaml or secrets.yaml")

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(deploy_src, DEPLOY_PATH)
        shutil.copy2(secrets_src, SECRETS_PATH)
        os.chmod(SECRETS_PATH, 0o600)
        print(f"Restored {DEPLOY_PATH} and {SECRETS_PATH}")

        config = load_yaml(deploy_src)
        entries = manifest.get("entries") or []
        if not entries:
            entries = [
                {"host_path": host, "bundle_path": bundle_path}
                for host, bundle_path in backup_path_entries(config)
            ]

        for item in entries:
            host_path = Path(str(item["host_path"]))
            bundle_path = str(item["bundle_path"])
            source = extracted / bundle_path
            if not source.exists():
                print(f"Skipping missing bundle path: {bundle_path}")
                continue
            print(f"Restoring {host_path} ← {bundle_path}")
            _install_entry(source, host_path)
            _maybe_fix_opencloud_ownership(host_path)

    print()
    print("Data restore complete.")
    if skip_apply:
        print("Skipped apply — run: bash apply.sh")
    else:
        print("Next: bash apply.sh will be run by restore-bundle.sh")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Portable OpenCloud backup bundles")
    sub = parser.add_subparsers(dest="command", required=True)

    create_parser = sub.add_parser("create", help="Create a portable backup archive")
    create_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output file (.tar.gz or .tar.zst)",
    )
    create_parser.add_argument(
        "--force",
        action="store_true",
        help="Create bundle even if OpenCloud containers are running",
    )

    restore_parser = sub.add_parser("restore", help="Restore from a portable backup archive")
    restore_parser.add_argument("archive", type=Path)
    restore_parser.add_argument(
        "--skip-apply",
        action="store_true",
        help="Only restore files; do not run apply.sh (used by restore-bundle.sh wrapper)",
    )

    args = parser.parse_args()

    try:
        if args.command == "create":
            sys.exit(cmd_create(output=args.output, force=args.force))
        if args.command == "restore":
            sys.exit(cmd_restore(args.archive, skip_apply=args.skip_apply))
    except (FileNotFoundError, FileExistsError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
