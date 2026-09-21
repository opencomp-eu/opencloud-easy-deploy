"""Tests for the OpenCloud dynamic shared-backup plan."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

from scripts.backup_plan import resolve_plan

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIB_BACKUP_PLAN = PROJECT_ROOT / "easydeploy-lib" / "python" / "backup_plan.py"


def test_backup_sh_defers_local_repo_creation_to_shared_lib():
    text = (PROJECT_ROOT / "backup.sh").read_text()

    assert "ensure_local_repo_dir" not in text
    assert 'mkdir -p "${BACKUP_REPO_PATH}"' not in text
    assert "easydeploy_backup_repo_create" in text


def test_plan_tracks_operator_paths_and_timer(tmp_path: Path):
    (tmp_path / "deploy.yaml").write_text(
        yaml.safe_dump(
            {
                "opencloud": {
                    "data_dir": "/srv/opencloud/data",
                    "config_dir": "/srv/opencloud/config",
                    "apps_dir": "/srv/opencloud/apps",
                },
                "weboffice": {"enabled": True, "type": "euro_office"},
            }
        )
    )
    plan = resolve_plan(tmp_path)
    assert plan["timer_name"] == "opencloud-easy-deploy-backup"
    entries = {item["as"]: item["path"] for item in plan["persistent_paths"]}
    assert entries["data/opencloud"] == "/srv/opencloud/data"
    assert entries["data/config"] == "/srv/opencloud/config"
    assert entries["data/apps"] == "/srv/opencloud/apps"
    assert entries["data/ldap_certs"] == "/srv/opencloud/ldap_certs"
    assert entries["data/euro-office"] == "/srv/opencloud/euro-office"


def test_shared_loader_emits_timer_name():
    result = subprocess.run(
        [
            sys.executable,
            str(LIB_BACKUP_PLAN),
            "--project-root",
            str(PROJECT_ROOT),
            "--emit-plan-json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    plan = json.loads(result.stdout)
    assert plan["service"] == "opencloud"
    assert plan["timer_name"] == "opencloud-easy-deploy-backup"
    assert plan["hooks"]["apply"] == "apply.sh"
