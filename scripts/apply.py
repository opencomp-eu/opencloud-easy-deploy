#!/usr/bin/env python3
"""opencloud-easy-deploy configuration engine."""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from scripts.backup import (
    backup_secret_keys,
    bootstrap_backup,
    print_backup_summary,
    validate_backup_config,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_DIR = PROJECT_ROOT / "opencloud-compose"
STATE_DIR = PROJECT_ROOT / ".opencloud-easy-deploy"
SECRETS_PATH = STATE_DIR / "secrets.yaml"
DEPLOY_PATH = PROJECT_ROOT / "deploy.yaml"
NETWORK_OVERLAY_PATH = STATE_DIR / "compose" / "network-fixups.yml"
INTEGRATION_DIR = STATE_DIR / "integration"
INTEGRATION_CADDY_FRAGMENT = INTEGRATION_DIR / "caddy.caddy"
DEFAULT_INTEGRATE_NETWORK = "easydeploy-net"
BITNAMI_OPENLDAP_UID = 1001
BITNAMI_OPENLDAP_GID = 1001
CADDY_DIR = PROJECT_ROOT / "caddy"
CADDY_TEMPLATE = CADDY_DIR / "Caddyfile.template"
CADDYFILE = CADDY_DIR / "Caddyfile"
PROXY_ROLE_TEMPLATE = (
    PROJECT_ROOT / "config-templates" / "opencloud" / "proxy.yaml.template"
)
CSP_TEMPLATE = PROJECT_ROOT / "config-templates" / "opencloud" / "csp.yaml.template"

SECRET_KEYS = (
    "INITIAL_ADMIN_PASSWORD",
    "EURO_OFFICE_JWT_SECRET",
    "LDAP_BIND_PASSWORD",
)

PRESERVED_SECRET_KEYS = frozenset(SECRET_KEYS)


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def proxy_mode(config: dict) -> str:
    mode = str((config.get("proxy") or {}).get("mode") or "standalone").strip().lower()
    if mode not in {"standalone", "integrate"}:
        raise ValueError("proxy.mode must be 'standalone' or 'integrate'")
    return mode


def load_yaml(path: Path) -> dict:
    with path.open() as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be a mapping")
    return data


def save_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        yaml.safe_dump(data, handle, default_flow_style=False, sort_keys=False)


def render_template(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    if "{{" in rendered:
        missing = sorted(set(part.split("}")[0] for part in rendered.split("{{")[1:]))
        raise ValueError(f"Unresolved template placeholders: {', '.join(missing)}")
    return rendered


def load_config(path: Path = DEPLOY_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path.name}. Copy deploy.yaml.example to deploy.yaml or run wizard.sh."
        )
    return load_yaml(path)


def validate_config(config: dict) -> None:
    opencloud = config.get("opencloud") or {}
    if not isinstance(opencloud, dict):
        raise ValueError("opencloud section must be a mapping")

    domain = str(opencloud.get("domain") or "").strip()
    if not domain or domain == "cloud.example.com":
        raise ValueError("opencloud.domain must be set to your real domain")

    for key in ("data_dir", "config_dir", "apps_dir"):
        value = str(opencloud.get(key) or "").strip()
        if not value:
            raise ValueError(f"opencloud.{key} must be set")

    proxy_type = (config.get("proxy") or {}).get("type", "caddy")
    if proxy_type != "caddy":
        raise ValueError("proxy.type must be 'caddy' in v1")

    auth = config.get("auth") or {}
    mode = str(auth.get("mode") or "builtin").strip().lower()
    if mode not in {"builtin", "oidc"}:
        raise ValueError("auth.mode must be 'builtin' or 'oidc'")

    if mode == "oidc":
        oidc = auth.get("oidc") or {}
        for field in ("issuer_url", "account_url", "domain", "client_id"):
            if not str(oidc.get(field) or "").strip():
                raise ValueError(f"auth.oidc.{field} is required when auth.mode=oidc")

    weboffice = config.get("weboffice") or {}
    if to_bool(weboffice.get("enabled")):
        office_type = str(weboffice.get("type") or "").strip().lower()
        if office_type not in {"euro_office", "collabora"}:
            raise ValueError("weboffice.type must be 'euro_office' or 'collabora'")
        if not str(weboffice.get("domain") or "").strip():
            raise ValueError("weboffice.domain is required when weboffice is enabled")

    validate_backup_config(config)
    proxy_mode(config)


def derive_compose_files(config: dict) -> list[str]:
    files = ["docker-compose.yml", "external-proxy/opencloud.yml"]
    if proxy_mode(config) == "standalone":
        files.append("../overlays/proxy/caddy.yml")

    weboffice = config.get("weboffice") or {}
    if to_bool(weboffice.get("enabled")):
        office_type = str(weboffice.get("type") or "euro_office").strip().lower()
        if office_type == "euro_office":
            files.extend(
                [
                    "weboffice/euro-office.yml",
                    "external-proxy/euro-office.yml",
                    "../overlays/weboffice/euro-office-production.yml",
                ]
            )
        elif office_type == "collabora":
            files.extend(
                ["weboffice/collabora.yml", "external-proxy/collabora.yml"]
            )

    auth_mode = str((config.get("auth") or {}).get("mode") or "builtin").lower()
    if auth_mode == "oidc":
        files.extend(["idm/external-idp.yml", "../overlays/idm/oidc-external.yml"])
        provider = str((config.get("auth") or {}).get("oidc", {}).get("provider") or "").lower()
        if provider == "authelia":
            files.append("idm/external-authelia.yml")
            files.append("../overlays/idm/authelia-provider.yml")

    modules = config.get("modules") or {}
    if to_bool(modules.get("search")):
        files.append("search/tika.yml")
    if to_bool(modules.get("antivirus")):
        files.append("antivirus/clamav.yml")
    if to_bool(modules.get("radicale")):
        files.append("radicale/radicale.yml")
    if to_bool(modules.get("monitoring")):
        files.append("monitoring/monitoring.yml")

    files.append("../.opencloud-easy-deploy/compose/network-fixups.yml")

    return files


def render_network_overlay(config: dict) -> None:
    """Stable Docker DNS names and host-gateway routes for cross-stack proxy/WOPI."""
    opencloud_domain = str(config["opencloud"]["domain"])
    weboffice = config.get("weboffice") or {}

    opencloud_service: dict[str, Any] = {
        "container_name": "opencloud",
        "extra_hosts": [f"{opencloud_domain}:host-gateway"],
    }
    if proxy_mode(config) == "integrate":
        opencloud_service["networks"] = ["opencloud-net", DEFAULT_INTEGRATE_NETWORK]

    services: dict[str, Any] = {"opencloud": opencloud_service}

    if to_bool(weboffice.get("enabled")):
        office_domain = str(weboffice.get("domain") or "")
        office_type = str(weboffice.get("type") or "euro_office").strip().lower()

        if office_domain:
            opencloud_service["extra_hosts"].append(f"{office_domain}:host-gateway")

        if office_type == "euro_office":
            euro_svc: dict[str, Any] = {
                "container_name": "euro-office",
                "extra_hosts": [f"{opencloud_domain}:host-gateway"],
            }
            if proxy_mode(config) == "integrate":
                euro_svc["networks"] = ["opencloud-net", DEFAULT_INTEGRATE_NETWORK]
            services["euro-office"] = euro_svc
        elif office_type == "collabora":
            collab_svc: dict[str, Any] = {
                "container_name": "collabora",
                "extra_hosts": [f"{opencloud_domain}:host-gateway"],
            }
            if proxy_mode(config) == "integrate":
                collab_svc["networks"] = ["opencloud-net", DEFAULT_INTEGRATE_NETWORK]
            services["collabora"] = collab_svc

    overlay: dict[str, Any] = {"services": services}
    overlay["networks"] = {
        "opencloud-net": {"external": True, "name": "opencloud-net"},
    }
    if proxy_mode(config) == "integrate":
        overlay["networks"][DEFAULT_INTEGRATE_NETWORK] = {
            "external": True,
            "name": DEFAULT_INTEGRATE_NETWORK,
        }

    NETWORK_OVERLAY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with NETWORK_OVERLAY_PATH.open("w") as handle:
        yaml.safe_dump(overlay, handle, default_flow_style=False)


def generate_secret(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def secret_keys_for_config(config: dict) -> tuple[str, ...]:
    return SECRET_KEYS + backup_secret_keys(config)


def create_or_update_secrets(config: dict, rotate: bool = False) -> dict[str, str]:
    existing: dict[str, str] = {}
    if SECRETS_PATH.exists() and not rotate:
        raw = load_yaml(SECRETS_PATH)
        existing = {k: str(v) for k, v in raw.items() if v is not None}

    secrets_map = dict(existing)
    for key in secret_keys_for_config(config):
        if rotate or not secrets_map.get(key):
            length = 16 if key == "LDAP_BIND_PASSWORD" else 24
            secrets_map[key] = generate_secret(length)

    save_yaml(SECRETS_PATH, secrets_map)
    os.chmod(SECRETS_PATH, 0o600)
    return secrets_map


def build_additional_services(config: dict) -> str:
    services: list[str] = []
    modules = config.get("modules") or {}
    if to_bool(modules.get("antivirus")):
        services.append("antivirus")
    return ",".join(services)


def build_env_vars(config: dict, secrets: dict[str, str]) -> dict[str, str]:
    opencloud = config["opencloud"]
    weboffice = config.get("weboffice") or {}
    auth = config.get("auth") or {}
    auth_mode = str(auth.get("mode") or "builtin").lower()
    oidc = auth.get("oidc") or {}

    env: dict[str, str] = {
        "LOG_DRIVER": "",
        "INSECURE": "false",
        "OC_LOG_LEVEL": "info",
        "COMPOSE_FILE": ":".join(derive_compose_files(config)),
        "COMPOSE_PATH_SEPARATOR": ":",
        "OC_DOCKER_IMAGE": str(opencloud.get("image") or "opencloudeu/opencloud-rolling"),
        "OC_DOCKER_TAG": str(opencloud.get("tag") or "latest"),
        "OC_DOMAIN": str(opencloud["domain"]),
        "DEMO_USERS": "false",
        "INITIAL_ADMIN_PASSWORD": secrets["INITIAL_ADMIN_PASSWORD"],
        "CHECK_FOR_UPDATES": "true",
        "LOG_LEVEL": "WARNING",
        "LOG_PRETTY": "true",
        "OC_CONFIG_DIR": str(opencloud["config_dir"]),
        "OC_DATA_DIR": str(opencloud["data_dir"]),
        "OC_APPS_DIR": str(opencloud["apps_dir"]),
        "DEFAULT_LANGUAGE": str(opencloud.get("language") or "en"),
        "START_ADDITIONAL_SERVICES": build_additional_services(config),
    }
    if proxy_mode(config) == "standalone":
        env["OCD_CADDYFILE"] = str(CADDYFILE.resolve())

    ldap_base = Path(str(opencloud["config_dir"])).parent
    env["LDAP_CERTS_DIR"] = str(ldap_base / "ldap_certs")
    env["LDAP_DATA_DIR"] = str(ldap_base / "ldap_data")

    data_root = Path(str(opencloud["data_dir"])).parent

    if to_bool(weboffice.get("enabled")):
        office_type = str(weboffice.get("type") or "euro_office").lower()
        if office_type == "euro_office":
            env["EURO_OFFICE_DOMAIN"] = str(weboffice["domain"])
            env["EURO_OFFICE_JWT_SECRET"] = secrets["EURO_OFFICE_JWT_SECRET"]
            env["EURO_OFFICE_DOCKER_IMAGE"] = ""
            env["EURO_OFFICE_DOCKER_TAG"] = "latest"
            env["EURO_OFFICE_DATA_DIR"] = str(data_root / "euro-office")
        elif office_type == "collabora":
            env["COLLABORA_DOMAIN"] = str(weboffice["domain"])
            env["COLLABORA_SSL_ENABLE"] = "false"
            env["COLLABORA_SSL_VERIFICATION"] = "true"

    if auth_mode == "oidc":
        role_mapping = oidc.get("role_mapping") or {}
        provider = str(oidc.get("provider") or "").lower()
        default_scopes = (
            "openid profile email groups"
            if provider == "authelia"
            else "openid profile email offline_access"
        )
        env.update(
            {
                "LDAP_BIND_PASSWORD": secrets["LDAP_BIND_PASSWORD"],
                "PROXY_ROLE_ASSIGNMENT_DRIVER": "oidc",
                "GRAPH_ASSIGN_DEFAULT_USER_ROLE": "false",
                "IDP_DOMAIN": str(oidc["domain"]),
                "IDP_ISSUER_URL": str(oidc["issuer_url"]),
                "IDP_ACCOUNT_URL": str(oidc["account_url"]),
                "PROXY_ROLE_ASSIGNMENT_OIDC_CLAIM": str(
                    oidc.get("role_claim") or "groups"
                ),
                "OC_OIDC_CLIENT_ID": str(oidc["client_id"]),
                "OC_OIDC_CLIENT_SCOPES": str(
                    oidc.get("client_scopes") or default_scopes
                ),
                "OC_SHARING_PUBLIC_SHARE_MUST_HAVE_PASSWORD": "false",
                "OC_SHARING_PUBLIC_WRITEABLE_SHARE_MUST_HAVE_PASSWORD": "false",
            }
        )
        env["_ROLE_MAPPING"] = yaml.safe_dump(role_mapping, default_flow_style=True)

    return env


def write_env_file(env_vars: dict[str, str], path: Path) -> None:
    lines = [
        "# Generated by opencloud-easy-deploy apply.sh — do not edit by hand.",
        "",
    ]
    for key, value in env_vars.items():
        if key.startswith("_"):
            continue
        escaped = value.replace("\n", "\\n")
        lines.append(f"{key}={escaped}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


def ensure_compose_submodule() -> None:
    if (COMPOSE_DIR / "docker-compose.yml").exists():
        return
    if not (PROJECT_ROOT / ".git").exists():
        raise FileNotFoundError(
            "opencloud-compose is missing. Run: git submodule update --init --recursive"
        )
    subprocess.run(
        ["git", "submodule", "update", "--init", "--recursive", "opencloud-compose"],
        cwd=PROJECT_ROOT,
        check=True,
    )
    if not (COMPOSE_DIR / "docker-compose.yml").exists():
        raise FileNotFoundError("Failed to initialize opencloud-compose submodule")


def bootstrap_ldap_tls(ldap_certs_dir: Path) -> None:
    """Pre-create LDAP TLS material on the host (Bitnami entrypoint cannot write bind mounts)."""
    key_path = ldap_certs_dir / "openldap.key"
    cert_path = ldap_certs_dir / "openldap.crt"
    if key_path.is_file() and cert_path.is_file():
        return

    ldap_certs_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:4096",
            "-keyout",
            str(key_path),
            "-out",
            str(cert_path),
            "-sha256",
            "-days",
            "365",
            "-batch",
            "-nodes",
            "-subj",
            "/CN=opencloud-ldap",
        ],
        check=True,
        capture_output=True,
    )
    key_path.chmod(0o640)
    cert_path.chmod(0o644)
    if os.geteuid() == 0:
        try:
            shutil.chown(key_path, BITNAMI_OPENLDAP_UID, BITNAMI_OPENLDAP_GID)
            shutil.chown(cert_path, BITNAMI_OPENLDAP_UID, BITNAMI_OPENLDAP_GID)
            shutil.chown(ldap_certs_dir, BITNAMI_OPENLDAP_UID, BITNAMI_OPENLDAP_GID)
        except OSError:
            key_path.chmod(0o644)


def bootstrap_config(config: dict) -> None:
    opencloud = config["opencloud"]
    config_dir = Path(str(opencloud["config_dir"]))
    apps_dir = Path(str(opencloud["apps_dir"]))
    data_dir = Path(str(opencloud["data_dir"]))
    ldap_base = config_dir.parent

    for directory in (
        config_dir,
        apps_dir,
        data_dir,
        data_dir.parent / "euro-office",
        ldap_base / "ldap_certs",
        ldap_base / "ldap_data",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    auth_mode = str((config.get("auth") or {}).get("mode") or "builtin").lower()
    if auth_mode == "oidc":
        bootstrap_ldap_tls(ldap_base / "ldap_certs")

    upstream_config = COMPOSE_DIR / "config" / "opencloud"
    if not upstream_config.is_dir():
        raise FileNotFoundError(f"Missing upstream config at {upstream_config}")

    for item in upstream_config.iterdir():
        target = config_dir / item.name
        if item.is_dir():
            if not target.exists():
                shutil.copytree(item, target)
        elif not target.exists():
            shutil.copy2(item, target)

    euro_registry = COMPOSE_DIR / "config" / "euro-office" / "app-registry.yaml"
    weboffice = config.get("weboffice") or {}
    if to_bool(weboffice.get("enabled")) and str(weboffice.get("type")) == "euro_office":
        euro_target = config_dir / "app-registry.yaml"
        if euro_registry.is_file() and not euro_target.exists():
            shutil.copy2(euro_registry, euro_target)


def render_proxy_yaml(config: dict) -> None:
    config_dir = Path(str(config["opencloud"]["config_dir"]))
    proxy_path = config_dir / "proxy.yaml"
    upstream_proxy = COMPOSE_DIR / "config" / "opencloud" / "proxy.yaml"

    auth_mode = str((config.get("auth") or {}).get("mode") or "builtin").lower()
    upstream_body = upstream_proxy.read_text() if upstream_proxy.is_file() else ""

    if auth_mode == "oidc":
        oidc = config["auth"]["oidc"]
        role_mapping = oidc.get("role_mapping") or {}
        role_block = render_template(
            PROXY_ROLE_TEMPLATE.read_text(),
            {
                "ROLE_CLAIM": str(oidc.get("role_claim") or "groups"),
                "ROLE_ADMIN": str(role_mapping.get("admin") or "opencloud-admin"),
                "ROLE_USER": str(role_mapping.get("user") or "opencloud-user"),
                "ROLE_GUEST": str(role_mapping.get("guest") or "opencloud-guest"),
            },
        )
        proxy_path.write_text(f"{role_block.rstrip()}\n{upstream_body.lstrip()}")
        render_csp_yaml(config)
    elif not proxy_path.exists():
        proxy_path.write_text(upstream_body)


def render_csp_yaml(config: dict) -> None:
    config_dir = Path(str(config["opencloud"]["config_dir"]))
    csp_path = config_dir / "csp.yaml"
    oidc = config["auth"]["oidc"]
    rendered = render_template(
        CSP_TEMPLATE.read_text(),
        {"IDP_DOMAIN": str(oidc["domain"])},
    )
    csp_path.write_text(rendered)


def build_caddy_site_blocks(config: dict) -> tuple[str, str]:
    opencloud_domain = str(config["opencloud"]["domain"])
    weboffice = config.get("weboffice") or {}

    oc_security_headers = """
    header {
        X-Content-Type-Options nosniff
        X-Frame-Options SAMEORIGIN
        Referrer-Policy strict-origin-when-cross-origin
        -Server
    }
    encode gzip
    log"""

    oc_block = f"""{opencloud_domain} {{
    reverse_proxy opencloud:9200 {{
        header_up X-Forwarded-Proto {{scheme}}
        header_up X-Forwarded-Host {{host}}
    }}{oc_security_headers}
}}"""

    euro_block = ""
    if to_bool(weboffice.get("enabled")):
        office_domain = str(weboffice.get("domain") or "")
        upstream = (
            "euro-office:80"
            if str(weboffice.get("type") or "euro_office") == "euro_office"
            else "collabora:9980"
        )
        if office_domain:
            euro_block = f"""
{office_domain} {{
    reverse_proxy {upstream} {{
        header_down -X-Frame-Options
    }}
    header {{
        X-Content-Type-Options nosniff
        Referrer-Policy strict-origin-when-cross-origin
        -Server
        -X-Frame-Options
        Content-Security-Policy "frame-ancestors 'self' https://{opencloud_domain}"
    }}
    encode gzip
    log
}}""".strip()

    return oc_block.strip(), euro_block


def build_caddy_fragment(config: dict) -> str:
    oc_block, euro_block = build_caddy_site_blocks(config)
    parts = ["# opencloud-easy-deploy", oc_block]
    if euro_block:
        parts.extend(["", "# opencloud-easy-deploy — web office", euro_block])
    return "\n".join(parts) + "\n"


def render_caddyfile(config: dict) -> None:
    oc_block, euro_block = build_caddy_site_blocks(config)
    rendered = render_template(
        CADDY_TEMPLATE.read_text(),
        {
            "OC_DOMAIN_BLOCK": oc_block,
            "EURO_OFFICE_DOMAIN_BLOCK": euro_block,
        },
    )
    CADDYFILE.write_text(rendered + "\n")


def render_integration_fragment(config: dict) -> None:
    INTEGRATION_DIR.mkdir(parents=True, exist_ok=True)
    INTEGRATION_CADDY_FRAGMENT.write_text(build_caddy_fragment(config))


def fix_data_permissions(config: dict) -> None:
    opencloud = config["opencloud"]
    config_dir = Path(str(opencloud["config_dir"]))
    ldap_base = config_dir.parent
    auth_mode = str((config.get("auth") or {}).get("mode") or "builtin").lower()
    paths = [
        config_dir,
        Path(str(opencloud["data_dir"])),
        Path(str(opencloud["apps_dir"])),
    ]
    if os.geteuid() != 0:
        return
    for path in paths:
        if path.exists():
            shutil.chown(path, user=1000, group=1000)
    if auth_mode == "oidc":
        for name in ("ldap_certs", "ldap_data"):
            path = ldap_base / name
            if path.exists():
                try:
                    shutil.chown(path, BITNAMI_OPENLDAP_UID, BITNAMI_OPENLDAP_GID)
                except OSError:
                    pass


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


def ensure_docker_network(name: str) -> None:
    result = subprocess.run(
        ["docker", "network", "inspect", name],
        capture_output=True,
    )
    if result.returncode != 0:
        subprocess.run(["docker", "network", "create", name], check=True)


def run_compose(directory: Path, *args: str, env: dict[str, str] | None = None) -> None:
    cmd = docker_compose_cmd() + list(args)
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    subprocess.run(cmd, cwd=directory, check=True, env=merged_env)


WOPI_DISCOVERY_PROBE = (
    "exec 3<>/dev/tcp/127.0.0.1/80 && "
    "printf 'GET /hosting/discovery HTTP/1.1\\r\\nHost: localhost\\r\\nConnection: close\\r\\n\\r\\n' >&3 && "
    "cat <&3 | head -1 | grep -q '200 OK'"
)


def wait_for_euro_office(timeout_seconds: int = 600) -> bool:
    """Wait until Euro Office serves WOPI discovery (not just /healthcheck)."""
    if subprocess.run(["docker", "inspect", "euro-office"], capture_output=True).returncode != 0:
        return False

    deadline = time.time() + timeout_seconds
    print("Waiting for Euro Office WOPI discovery (first start may take several minutes)…")
    while time.time() < deadline:
        result = subprocess.run(
            ["docker", "exec", "euro-office", "bash", "-c", WOPI_DISCOVERY_PROBE],
            capture_output=True,
        )
        if result.returncode == 0:
            print("Euro Office WOPI discovery is ready.")
            return True
        time.sleep(10)

    print(
        "Warning: Euro Office WOPI discovery did not become ready in time.",
        file=sys.stderr,
    )
    return False


def restart_opencloud_if_present() -> None:
    if subprocess.run(["docker", "inspect", "opencloud"], capture_output=True).returncode == 0:
        print("Restarting OpenCloud to refresh WOPI discovery…")
        subprocess.run(["docker", "restart", "opencloud"], check=True)


def stop_legacy_caddy() -> None:
    """Remove Caddy from the old separate compose project if it still exists."""
    legacy_compose = CADDY_DIR / "docker-compose.yml"
    if not legacy_compose.is_file():
        return
    subprocess.run(
        docker_compose_cmd() + ["-f", str(legacy_compose), "down"],
        cwd=CADDY_DIR,
        capture_output=True,
    )


def stop_opencloud_caddy() -> None:
    if subprocess.run(["docker", "inspect", "opencloud_caddy"], capture_output=True).returncode == 0:
        print("Stopping standalone opencloud_caddy (integrate mode uses easydeploy-engine)…")
        subprocess.run(["docker", "stop", "opencloud_caddy"], check=False)
        subprocess.run(["docker", "rm", "opencloud_caddy"], check=False)


def reconcile_runtime(env_path: Path, config: dict) -> None:
    env = {}
    if env_path.is_file():
        for line in env_path.read_text().splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()

    ensure_docker_network("opencloud-net")
    if proxy_mode(config) == "integrate":
        ensure_docker_network(DEFAULT_INTEGRATE_NETWORK)
        stop_opencloud_caddy()
    stop_legacy_caddy()

    print("Pulling OpenCloud stack images…")
    run_compose(COMPOSE_DIR, "pull", env=env)

    if proxy_mode(config) == "integrate":
        print("Starting OpenCloud stack (no local Caddy — use easydeploy-engine)…")
    else:
        print("Starting OpenCloud stack (includes Caddy)…")
    run_compose(COMPOSE_DIR, "up", "-d", "--wait", "--force-recreate", env=env)

    weboffice = config.get("weboffice") or {}
    if to_bool(weboffice.get("enabled")) and str(weboffice.get("type") or "") == "euro_office":
        if wait_for_euro_office():
            restart_opencloud_if_present()


def print_summary(config: dict) -> None:
    domain = config["opencloud"]["domain"]
    print()
    print("=== Deployment summary ===")
    print(f"OpenCloud URL: https://{domain}")
    weboffice = config.get("weboffice") or {}
    if to_bool(weboffice.get("enabled")):
        print(f"Web office URL: https://{weboffice.get('domain')}")
    print()
    print("DNS records required (A/AAAA):")
    print(f"  - {domain}")
    if to_bool(weboffice.get("enabled")):
        print(f"  - {weboffice.get('domain')}")

    if proxy_mode(config) == "integrate":
        print()
        print(f"Proxy mode: integrate (fragment: {INTEGRATION_CADDY_FRAGMENT})")
        print("Run easydeploy-engine apply.sh after enabling OpenCloud in engine.yaml.")

    auth_mode = str((config.get("auth") or {}).get("mode") or "builtin").lower()
    if auth_mode == "builtin":
        print()
        print("Built-in auth: log in with username 'admin' and the generated admin password.")
        print(f"Admin password stored in: {SECRETS_PATH}")
    else:
        oidc = config["auth"]["oidc"]
        print()
        print("OIDC auth: configure your IdP with these redirect URIs (strict):")
        print(f"  - https://{domain}/")
        print(f"  - https://{domain}/web-oidc-callback")
        print(f"  - https://{domain}/oidc-callback.html")
        print(f"  - https://{domain}/oidc-silent-redirect.html")
        print()
        print("Create IdP groups matching role_mapping in deploy.yaml:")
        for role, group in (oidc.get("role_mapping") or {}).items():
            print(f"  - {group} → {role}")


def check_docker_available() -> None:
    if not shutil.which("docker"):
        raise RuntimeError("Docker is not installed or not in PATH")


def apply(
    *,
    no_reconcile_runtime: bool = False,
    rotate_secrets: bool = False,
) -> None:
    check_docker_available()
    config = load_config()
    validate_config(config)
    ensure_compose_submodule()

    secret_values = create_or_update_secrets(config, rotate=rotate_secrets)
    bootstrap_config(config)
    render_network_overlay(config)
    render_proxy_yaml(config)
    if proxy_mode(config) == "integrate":
        render_integration_fragment(config)
    else:
        render_caddyfile(config)
    bootstrap_backup(config, secret_values)

    env_vars = build_env_vars(config, secret_values)
    env_path = COMPOSE_DIR / ".env"
    write_env_file(env_vars, env_path)
    fix_data_permissions(config)

    if no_reconcile_runtime:
        print("Skipping runtime reconcile (--no-reconcile-runtime).")
    else:
        reconcile_runtime(env_path, config)

    print_summary(config)
    print_backup_summary(config)


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply opencloud-easy-deploy configuration")
    parser.add_argument(
        "--no-reconcile-runtime",
        action="store_true",
        help="Render configs only; do not start containers",
    )
    parser.add_argument(
        "--rotate-secrets",
        action="store_true",
        help="Regenerate all secrets (destructive)",
    )
    args = parser.parse_args()

    try:
        apply(
            no_reconcile_runtime=args.no_reconcile_runtime,
            rotate_secrets=args.rotate_secrets,
        )
    except (FileNotFoundError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
