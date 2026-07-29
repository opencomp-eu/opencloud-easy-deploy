#!/usr/bin/env bash
# wizard.sh — interactive setup for opencloud-easy-deploy
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

DEPLOY_YAML="${SCRIPT_DIR}/deploy.yaml"

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
	local base_domain proceed

	print_banner
	echo -e "  Press Enter to accept a ${CYAN}[default]${RESET}.\n"

	ask domain "OpenCloud domain (e.g. cloud.example.com)" "cloud.example.com"
	base_domain="$(base_domain_from_host "$domain")"

	ask data_root "Data root directory" "/var/lib/opencloud"

	echo
	echo -e "${BOLD}  Authentication${RESET}"
	ask auth_mode "Auth mode: builtin or oidc" "builtin"
	auth_mode="${auth_mode,,}"
	if [[ "$auth_mode" != "builtin" && "$auth_mode" != "oidc" ]]; then
		die "auth mode must be 'builtin' or 'oidc'"
	fi

	admin_password=""
	if [[ "$auth_mode" == "builtin" ]]; then
		ask_secret admin_password "Admin password (leave empty to auto-generate on apply)"
	fi

	oidc_issuer=""
	oidc_account=""
	oidc_domain=""
	oidc_client_id="opencloud"
	role_admin="opencloud-admin"
	role_user="opencloud-user"
	role_guest="opencloud-guest"

	if [[ "$auth_mode" == "oidc" ]]; then
		echo
		echo -e "${BOLD}  External OIDC (Authentik, Keycloak, …)${RESET}"
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
	echo -e "${BOLD}  Summary${RESET}"
	echo "  OpenCloud:     https://${domain}"
	if [[ "$weboffice_enabled" == "y" ]]; then
		echo "  Euro Office:   https://${weboffice_domain}"
	fi
	echo "  Auth:          ${auth_mode}"
	echo "  Data root:     ${data_root}"
	echo
	echo "  Ensure DNS A/AAAA records point to this server before continuing."
	echo

	ask_yn proceed "Write deploy.yaml and deploy now?" "y"
	[[ "$proceed" == "y" ]] || {
		info "Cancelled."
		exit 0
	}

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
    role_admin=${role_admin@Q},
    role_user=${role_user@Q},
    role_guest=${role_guest@Q},
    weboffice_enabled=${weboffice_enabled@Q} == "y",
    weboffice_domain=${weboffice_domain@Q} or None,
    modules_search=${modules_search@Q} == "y",
    modules_antivirus=${modules_antivirus@Q} == "y",
    modules_radicale=${modules_radicale@Q} == "y",
    modules_monitoring=${modules_monitoring@Q} == "y",
    path=Path(${DEPLOY_YAML@Q}),
)
PY

	success "Wrote ${DEPLOY_YAML}"
}

main() {
	if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
		echo "Usage: bash wizard.sh"
		exit 0
	fi

	bash "${SCRIPT_DIR}/ensure_dependencies.sh"
	gather_config
	bash "${SCRIPT_DIR}/apply.sh"
}

main "$@"
