#!/usr/bin/env bash
# uninstall.sh — remove generated runtime state (keeps deploy.yaml and data dirs)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

ASSUME_YES="false"

print_help() {
	cat <<EOF
Usage:
  bash uninstall.sh [--yes]

Removes containers and generated config files. Preserves deploy.yaml and data directories.
EOF
}

confirm_uninstall() {
	echo
	warn "This stops services and removes generated runtime files from this repository."
	info "deploy.yaml and /var/lib/opencloud (or your configured data paths) are preserved."

	local confirm
	ask_yn confirm "Continue with uninstall?" "n"
	[[ "$confirm" == "y" ]] || return 1

	local final_confirm
	ask_yn final_confirm "Final confirmation?" "n"
	[[ "$final_confirm" == "y" ]]
}

remove_path_if_present() {
	local rel_path="$1"
	local abs_path="${SCRIPT_DIR}/${rel_path}"
	if [[ -e "$abs_path" ]]; then
		rm -rf "$abs_path"
		info "Removed ${rel_path}"
	fi
}

main() {
	while [[ $# -gt 0 ]]; do
		case "$1" in
			-y|--yes) ASSUME_YES="true" ;;
			-h|--help) print_help; exit 0 ;;
			*) die "Unknown argument: $1" ;;
		esac
		shift
	done

	if [[ "$ASSUME_YES" != "true" ]]; then
		confirm_uninstall || {
			info "Uninstall cancelled."
			exit 0
		}
	fi

	bash "${SCRIPT_DIR}/stop.sh" || warn "stop.sh reported an error; continuing."

	remove_path_if_present "opencloud-compose/.env"
	remove_path_if_present "caddy/Caddyfile"
	remove_path_if_present ".opencloud-easy-deploy"

	if docker network inspect opencloud-net &>/dev/null; then
		if docker network rm opencloud-net &>/dev/null; then
			info "Removed Docker network opencloud-net"
		else
			warn "Could not remove opencloud-net (containers may still be attached)."
		fi
	fi

	success "Uninstall cleanup complete."
	info "Preserved: deploy.yaml and OpenCloud data directories"
}

main "$@"
