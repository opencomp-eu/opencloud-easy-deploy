#!/usr/bin/env python3
"""Dynamic backup plan for opencloud-easy-deploy.

The OpenCloud data/config/apps paths and optional Euro Office path are operator
settings, so the shared backup loader resolves them from deploy.yaml at runtime.
The legacy paths retain compatibility with pre-shared-lib archives.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

STATE_DIR = ".opencloud-easy-deploy"
DEFAULT_DATA_DIR = "/var/lib/opencloud/data"
DEFAULT_CONFIG_DIR = "/var/lib/opencloud/config"
DEFAULT_APPS_DIR = "/var/lib/opencloud/apps"


def _load_config(project_root: Path) -> dict[str, Any]:
    path = Path(project_root) / "deploy.yaml"
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    return data if isinstance(data, dict) else {}


def _enabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def resolve_plan(project_root: Path) -> dict[str, Any]:
    project_root = Path(project_root)
    config = _load_config(project_root)
    opencloud = config.get("opencloud") or {}
    data_dir = str(opencloud.get("data_dir") or DEFAULT_DATA_DIR).strip()
    config_dir = str(opencloud.get("config_dir") or DEFAULT_CONFIG_DIR).strip()
    apps_dir = str(opencloud.get("apps_dir") or DEFAULT_APPS_DIR).strip()
    ldap_root = Path(config_dir).parent

    persistent_paths: list[dict[str, str]] = [
        {"path": "deploy.yaml", "as": "deploy.yaml"},
        {"path": STATE_DIR, "as": STATE_DIR},
        {"path": data_dir, "as": "data/opencloud"},
        {"path": config_dir, "as": "data/config"},
        {"path": apps_dir, "as": "data/apps"},
        {"path": str(ldap_root / "ldap_certs"), "as": "data/ldap_certs"},
        {"path": str(ldap_root / "ldap_data"), "as": "data/ldap_data"},
    ]

    weboffice = config.get("weboffice") or {}
    if _enabled(weboffice.get("enabled")) and str(weboffice.get("type") or "") == "euro_office":
        persistent_paths.append(
            {"path": str(Path(data_dir).parent / "euro-office"), "as": "data/euro-office"}
        )

    # The old Borg runner archived /backup-root/* directly. These names are
    # consumed only when restoring a legacy manifest format (<=2).
    legacy_paths = [
        "deploy.yaml",
        STATE_DIR,
        "data",
        "config",
        "apps",
        "ldap_certs",
        "ldap_data",
        "secrets/secrets.yaml",
        "secrets/deploy.yaml",
    ]
    if persistent_paths[-1]["as"] == "data/euro-office":
        legacy_paths.append("euro-office")

    return {
        "service": "opencloud",
        "archive_prefix": "opencloud",
        "timer_name": "opencloud-easy-deploy-backup",
        "state_dir": STATE_DIR,
        "secrets_file": f"{STATE_DIR}/secrets.yaml",
        "hooks": {"apply": "apply.sh", "stop": "stop.sh", "start": "start.sh"},
        "persistent_paths": persistent_paths,
        "docker_volumes": [],
        "databases": [],
        "legacy_persistent_paths": legacy_paths,
    }
