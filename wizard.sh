#!/usr/bin/env bash
# wizard.sh — interactive setup for opencloud-easy-deploy
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

DEPLOY_YAML="${SCRIPT_DIR}/deploy.yaml"
NO_APPLY=0
PROXY_MODE=""

usage() {
	echo "Usage: bash wizard.sh [--from-engine] [--no-apply] [--proxy-mode standalone|integrate]"
}

while [[ $# -gt 0 ]]; do
	case "$1" in
		--help|-h)
			usage
			exit 0
			;;
		--from-engine)
			NO_APPLY=1
			PROXY_MODE="integrate"
			shift
			;;
		--no-apply)
			NO_APPLY=1
			shift
			;;
		--proxy-mode)
			PROXY_MODE="${2:-}"
			shift 2
			;;
		--proxy-mode=*)
			PROXY_MODE="${1#*=}"
			shift
			;;
		*)
			die "Unknown option: $1"
			;;
	esac
done

print_banner() {
	echo
	echo -e "${BOLD}  OpenCloud Easy Deploy — Setup Wizard${RESET}"
	echo -e "  ─────────────────────────────────────────────────────"
	echo
}

gather_config() {
	local domain data_root auth_mode admin_password
	local oidc_issuer oidc_account oidc_domain oidc_client_id
	local role_admin role_user role_guest
	local weboffice_enabled weboffice_domain
	local modules_search modules_antivirus modules_radicale modules_monitoring
	local base_domain proceed proxy_mode use_local_authelia oidc_provider
	local authelia_deploy authelia_domain

	print_banner
	echo -e "  Press Enter to accept a ${CYAN}[default]${RESET}.\n"

	ask domain "OpenCloud domain (e.g. cloud.example.com)" "cloud.example.com"
	base_domain="$(base_domain_from_host "$domain")"

	ask data_root "Data root directory" "/var/lib/opencloud"

	echo
	echo -e "${BOLD}  Authentication${RESET}"
	authelia_deploy="$(cd "${SCRIPT_DIR}/.." && pwd)/authelia-easy-deploy/deploy.yaml"
	use_local_authelia="n"
	oidc_provider=""
	if [[ -f "$authelia_deploy" ]]; then
		ask_yn use_local_authelia "Use Authelia on this VPS as the OpenCloud IdP?" "y"
	fi
	if [[ "$use_local_authelia" == "y" ]]; then
		auth_mode="oidc"
		oidc_provider="authelia"
		authelia_domain="$(uv run python - <<PY
import yaml
from pathlib import Path
data = yaml.safe_load(Path(${authelia_deploy@Q}).read_text()) or {}
print((data.get("authelia") or {}).get("domain") or "")
PY
)"
		if [[ -z "$authelia_domain" ]]; then
			die "Could not read authelia.domain from ${authelia_deploy}"
		fi
		oidc_issuer="https://${authelia_domain}"
		oidc_account="https://${authelia_domain}/"
		oidc_domain="$authelia_domain"
		info "Using Authelia at https://${authelia_domain} (engine will register the OIDC client)."
	else
		ask auth_mode "Auth mode: builtin or oidc" "builtin"
		auth_mode="${auth_mode,,}"
		if [[ "$auth_mode" != "builtin" && "$auth_mode" != "oidc" ]]; then
			die "auth mode must be 'builtin' or 'oidc'"
		fi
	fi

	admin_password=""
	if [[ "$auth_mode" == "builtin" ]]; then
		ask_secret admin_password "Admin password (leave empty to auto-generate on apply)"
	fi

	oidc_client_id="opencloud"
	role_admin="opencloud-admin"
	role_user="opencloud-user"
	role_guest="opencloud-guest"
	if [[ "$use_local_authelia" != "y" ]]; then
		oidc_issuer=""
		oidc_account=""
		oidc_domain=""
	fi

	if [[ "$auth_mode" == "oidc" && "$use_local_authelia" != "y" ]]; then
		echo
		echo -e "${BOLD}  External OIDC (Authentik, Keycloak, remote Authelia, …)${RESET}"
		ask oidc_issuer "OIDC issuer URL" "https://authentik.${base_domain}/application/o/opencloud/"
		ask oidc_account "Account settings URL" "https://authentik.${base_domain}/if/user/"
		ask oidc_domain "IdP domain (for CSP)" "authentik.${base_domain}"
		ask oidc_client_id "OIDC client ID" "opencloud"
		ask role_admin "Admin group name" "opencloud-admin"
		ask role_user "User group name" "opencloud-user"
		ask role_guest "Guest group name" "opencloud-guest"
	fi

	echo
	echo -e "${BOLD}  Web office${RESET}"
	ask_yn weboffice_enabled "Enable Euro Office?" "y"
	weboffice_domain=""
	if [[ "$weboffice_enabled" == "y" ]]; then
		ask weboffice_domain "Euro Office domain" "eurooffice.${base_domain}"
	fi

	echo
	echo -e "${BOLD}  Optional modules${RESET}"
	ask_yn modules_search "Enable full-text search (Tika)?" "n"
	ask_yn modules_antivirus "Enable ClamAV antivirus?" "n"
	ask_yn modules_radicale "Enable Radicale (Cal/CardDAV)?" "n"
	ask_yn modules_monitoring "Enable monitoring endpoints?" "n"

	echo
	echo -e "${BOLD}  Reverse proxy${RESET}"
	if [[ -n "${PROXY_MODE}" ]]; then
		proxy_mode="${PROXY_MODE,,}"
		info "Proxy mode: ${proxy_mode} (set by easydeploy-engine)"
	else
		ask proxy_mode "Proxy mode: standalone or integrate" "$([[ "$use_local_authelia" == "y" ]] && echo integrate || echo standalone)"
		proxy_mode="${proxy_mode,,}"
	fi
	if [[ "$proxy_mode" != "standalone" && "$proxy_mode" != "integrate" ]]; then
		die "proxy mode must be 'standalone' or 'integrate'"
	fi

	echo
	echo -e "${BOLD}  Summary${RESET}"
	echo "  OpenCloud:     https://${domain}"
	if [[ "$weboffice_enabled" == "y" ]]; then
		echo "  Euro Office:   https://${weboffice_domain}"
	fi
	echo "  Auth:          ${auth_mode}${oidc_provider:+ (${oidc_provider})}"
	echo "  Data root:     ${data_root}"
	echo "  Proxy mode:    ${proxy_mode}"
	echo
	echo "  Ensure DNS A/AAAA records point to this server before continuing."
	echo

	if [[ "${NO_APPLY}" == "1" ]]; then
		ask_yn proceed "Write deploy.yaml?" "y"
	else
		ask_yn proceed "Write deploy.yaml and deploy now?" "y"
	fi
	[[ "$proceed" == "y" ]] || {
		info "Cancelled."
		exit 0
	}

	cd "${SCRIPT_DIR}"
	uv run python - <<PY
from scripts.config_edit import update_from_wizard
from pathlib import Path

update_from_wizard(
    domain=${domain@Q},
    data_root=${data_root@Q},
    auth_mode=${auth_mode@Q},
    admin_password=${admin_password@Q} or None,
    oidc_issuer=${oidc_issuer@Q} or None,
    oidc_account_url=${oidc_account@Q} or None,
    oidc_domain=${oidc_domain@Q} or None,
    oidc_client_id=${oidc_client_id@Q} or None,
    oidc_provider=${oidc_provider@Q} or None,
    role_admin=${role_admin@Q},
    role_user=${role_user@Q},
    role_guest=${role_guest@Q},
    weboffice_enabled=${weboffice_enabled@Q} == "y",
    weboffice_domain=${weboffice_domain@Q} or None,
    modules_search=${modules_search@Q} == "y",
    modules_antivirus=${modules_antivirus@Q} == "y",
    modules_radicale=${modules_radicale@Q} == "y",
    modules_monitoring=${modules_monitoring@Q} == "y",
    proxy_mode=${proxy_mode@Q},
    path=Path(${DEPLOY_YAML@Q}),
)
PY

	success "Wrote ${DEPLOY_YAML}"
}

main() {
	bash "${SCRIPT_DIR}/ensure-dependencies.sh"
	gather_config
	if [[ "${NO_APPLY}" == "1" ]]; then
		info "Skipping apply (--no-apply / --from-engine). easydeploy-engine will apply."
		return 0
	fi
	bash "${SCRIPT_DIR}/apply.sh"
}

main "$@"
