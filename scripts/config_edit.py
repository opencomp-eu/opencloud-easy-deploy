#!/usr/bin/env python3
"""Read and write deploy.yaml for wizard and CLI tooling."""

from __future__ import annotations

import argparse
import os
import shlex
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEPLOY_PATH = PROJECT_ROOT / "deploy.yaml"


def load_or_init(path: Path = DEFAULT_DEPLOY_PATH) -> dict:
    if not path.exists():
        example = PROJECT_ROOT / "deploy.yaml.example"
        if example.is_file():
            with example.open() as handle:
                return yaml.safe_load(handle) or {}
        return {}

    with path.open() as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("deploy.yaml root must be a mapping")
    return data


def save(path: Path, data: dict) -> None:
    with path.open("w") as handle:
        yaml.safe_dump(data, handle, default_flow_style=False, sort_keys=False)


def read_kanidm_domain(deploy_path: Path) -> str:
    if not deploy_path.is_file():
        return ""
    with deploy_path.open() as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        return ""
    return str((data.get("kanidm") or {}).get("domain") or "").strip()


def discover_local_kanidm(opencloud_root: Path = PROJECT_ROOT) -> dict[str, str]:
    """Find a sibling (or engine-exported) Kanidm deploy.yaml and portal domain."""
    candidates: list[Path] = []
    env_deploy = str(os.environ.get("EASYDEPLOY_KANIDM_DEPLOY") or "").strip()
    if env_deploy:
        candidates.append(Path(env_deploy).expanduser())
    candidates.append((opencloud_root.parent / "kanidm-easy-deploy" / "deploy.yaml").resolve())

    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        domain = read_kanidm_domain(resolved)
        if domain:
            return {"domain": domain, "deploy": str(resolved)}

    env_domain = str(os.environ.get("EASYDEPLOY_KANIDM_DOMAIN") or "").strip()
    if env_domain:
        return {"domain": env_domain, "deploy": env_deploy}
    return {}


def emit_local_kanidm(opencloud_root: Path = PROJECT_ROOT) -> str:
    found = discover_local_kanidm(opencloud_root)
    domain = found.get("domain", "")
    deploy = found.get("deploy", "")
    return (
        f"LOCAL_KANIDM_DOMAIN={shlex.quote(domain)}\n"
        f"LOCAL_KANIDM_DEPLOY={shlex.quote(deploy)}\n"
    )


def update_from_wizard(
    *,
    domain: str,
    data_root: str,
    auth_mode: str,
    admin_password: str | None,
    oidc_issuer: str | None,
    oidc_account_url: str | None,
    oidc_domain: str | None,
    oidc_client_id: str | None,
    oidc_provider: str | None,
    role_admin: str,
    role_user: str,
    role_guest: str,
    weboffice_enabled: bool,
    weboffice_domain: str | None,
    modules_search: bool,
    modules_antivirus: bool,
    modules_radicale: bool,
    modules_monitoring: bool,
    proxy_mode: str = "standalone",
    path: Path = DEFAULT_DEPLOY_PATH,
) -> None:
    config = load_or_init(path)

    opencloud = config.setdefault("opencloud", {})
    opencloud["domain"] = domain
    opencloud.setdefault("image", "opencloudeu/opencloud-rolling")
    opencloud.setdefault("tag", "7.2.0")
    opencloud.setdefault("admin_username", "admin")
    opencloud.setdefault("language", "en")
    opencloud["data_dir"] = f"{data_root.rstrip('/')}/data"
    opencloud["config_dir"] = f"{data_root.rstrip('/')}/config"
    opencloud["apps_dir"] = f"{data_root.rstrip('/')}/apps"

    config["proxy"] = {
        "type": "caddy",
        "mode": proxy_mode,
        "integrate": {"network": "easydeploy-net"},
    }
    config["auth"] = {
        "mode": auth_mode,
        "oidc": {
            "provider": oidc_provider or "",
            "issuer_url": oidc_issuer or "",
            "account_url": oidc_account_url or "",
            "domain": oidc_domain or "",
            "client_id": oidc_client_id or "opencloud",
            "client_scopes": (
                "openid profile email groups groups_name"
                if (oidc_provider or "").lower() == "kanidm"
                else "openid profile email offline_access"
            ),
            "role_claim": "groups",
            "role_mapping": {
                "admin": role_admin,
                "user": role_user,
                "guest": role_guest,
            },
        },
    }

    if weboffice_enabled and weboffice_domain:
        config["weboffice"] = {
            "enabled": True,
            "type": "euro_office",
            "domain": weboffice_domain,
        }
    else:
        config["weboffice"] = {"enabled": False, "type": "none", "domain": ""}

    config["modules"] = {
        "search": modules_search,
        "antivirus": modules_antivirus,
        "radicale": modules_radicale,
        "monitoring": modules_monitoring,
    }

    save(path, config)

    if auth_mode == "builtin" and admin_password:
        secrets_path = PROJECT_ROOT / ".opencloud-easy-deploy" / "secrets.yaml"
        secrets_path.parent.mkdir(parents=True, exist_ok=True)
        secrets = {}
        if secrets_path.is_file():
            with secrets_path.open() as handle:
                secrets = yaml.safe_load(handle) or {}
        secrets["INITIAL_ADMIN_PASSWORD"] = admin_password
        with secrets_path.open("w") as handle:
            yaml.safe_dump(secrets, handle, default_flow_style=False)
        secrets_path.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description="Edit deploy.yaml")
    parser.add_argument("--show", action="store_true", help="Print deploy.yaml as JSON")
    parser.add_argument("--print-local-kanidm", action="store_true")
    parser.add_argument("--path", type=Path, default=DEFAULT_DEPLOY_PATH)
    args = parser.parse_args()

    if args.print_local_kanidm:
        print(emit_local_kanidm(PROJECT_ROOT), end="")
        return

    if args.show:
        import json

        print(json.dumps(load_or_init(args.path), indent=2))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
