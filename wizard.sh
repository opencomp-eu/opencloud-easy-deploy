#!/usr/bin/env bash
# wizard.sh — interactive setup for opencloud-easy-deploy
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

EASYDEPLOY_INVOKE_ARGS=("$@")
clear_parent_python_env

DEPLOY_YAML="${SCRIPT_DIR}/deploy.yaml"
NO_APPLY=0
PROXY_MODE=""
FROM_ENGINE=0

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
			FROM_ENGINE=1
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
	local authelia_domain default_idp
	local LOCAL_AUTHELIA_DOMAIN="" LOCAL_AUTHELIA_DEPLOY=""

	print_banner
	echo -e "  Press Enter to accept a ${CYAN}[default]${RESET}.\n"
	print_data_dir_hint
	cd "${SCRIPT_DIR}"

	ask domain "OpenCloud domain (e.g. cloud.example.com)" "cloud.example.com"
	base_domain="$(base_domain_from_host "$domain")"

	ask data_root "Data root directory" "$(default_data_dir opencloud)"

	echo
	echo -e "${BOLD}  Authentication${RESET}"
	eval "$(uv run python -m scripts.config_edit --print-local-authelia)"
	use_local_authelia="n"
	oidc_provider=""
	authelia_domain="${LOCAL_AUTHELIA_DOMAIN:-}"
	if [[ -n "$authelia_domain" ]]; then
		if [[ "${FROM_ENGINE}" == "1" ]]; then
			use_local_authelia="y"
			info "Using Authelia on this VPS at https://${authelia_domain}."
		else
			ask_yn use_local_authelia "Use Authelia at https://${authelia_domain} as the OpenCloud IdP?" "y"
		fi
	fi
	if [[ "$use_local_authelia" == "y" ]]; then
		if [[ -z "$authelia_domain" ]]; then
			die "Authelia was selected but no portal domain was found."
		fi
		auth_mode="oidc"
		oidc_provider="authelia"
		oidc_issuer="https://${authelia_domain}"
		oidc_account="https://${authelia_domain}/"
		oidc_domain="$authelia_domain"
		info "OIDC issuer: ${oidc_issuer} (engine will register the OIDC client)."
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
		default_idp="${authelia_domain:-auth.${base_domain}}"
		echo
		echo -e "${BOLD}  OIDC issuer (Authelia, Authentik, Keycloak, …)${RESET}"
		echo "  Authelia issuer is the portal origin, e.g. https://auth.${base_domain}"
		ask oidc_issuer "OIDC issuer URL" "https://${default_idp}"
		ask oidc_account "Account settings URL" "https://${default_idp}/"
		ask oidc_domain "IdP domain (for CSP)" "${default_idp}"
		ask oidc_client_id "OIDC client ID" "opencloud"
		ask oidc_provider "Provider: authelia, authentik, keycloak, or other" "authelia"
		oidc_provider="${oidc_provider,,}"
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
	ensure_docker_group_session "${EASYDEPLOY_INVOKE_ARGS[@]}"
	cd "${SCRIPT_DIR}"
	gather_config
	if [[ "${NO_APPLY}" == "1" ]]; then
		info "Skipping apply (--no-apply / --from-engine). easydeploy-engine will apply."
		return 0
	fi
	bash "${SCRIPT_DIR}/apply.sh"
}

main "$@"
