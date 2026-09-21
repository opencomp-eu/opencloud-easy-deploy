#!/usr/bin/env bash
# update.sh — pull latest opencloud-compose and container images
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

verbose="false"
for arg in "$@"; do
	case "$arg" in
		-v|--verbose) verbose="true" ;;
		-h|--help)
			echo "Usage: bash update.sh [--verbose]"
			echo "Pull submodule + images, re-apply, and restart. Default output is a short summary."
			exit 0
			;;
	esac
done

if [[ "$verbose" == "true" ]]; then
	export EASYDEPLOY_VERBOSE=1
	unset EASYDEPLOY_QUIET || true
else
	export EASYDEPLOY_QUIET=1
	export COMPOSE_PROGRESS=quiet
	export DOCKER_CLI_HINTS=false
	echo "Updating OpenCloud…"
fi

info "Updating opencloud-compose submodule…"
if [[ -d "${SCRIPT_DIR}/.git" ]]; then
	if [[ "$verbose" == "true" ]]; then
		git -C "${SCRIPT_DIR}" submodule update --remote --merge opencloud-compose || \
			warn "Submodule update failed; continuing with existing checkout."
	else
		git -C "${SCRIPT_DIR}" submodule update --quiet --remote --merge opencloud-compose >/dev/null 2>&1 || \
			warn "Submodule update failed; continuing with existing checkout."
	fi
fi

info "Stopping services…"
bash "${SCRIPT_DIR}/stop.sh"

IFS=' ' read -ra DOCKER_COMPOSE <<< "$(docker_compose_cmd)"
COMPOSE_ENV="${SCRIPT_DIR}/opencloud-compose/.env"

if [[ -f "$COMPOSE_ENV" ]]; then
	load_deploy_env "$COMPOSE_ENV"
fi

info "Pulling updated images…"
if [[ "$verbose" == "true" ]]; then
	(cd "${SCRIPT_DIR}/opencloud-compose" && "${DOCKER_COMPOSE[@]}" pull)
	docker pull caddy:2-alpine
else
	(cd "${SCRIPT_DIR}/opencloud-compose" && "${DOCKER_COMPOSE[@]}" pull --quiet)
	docker pull -q caddy:2-alpine >/dev/null
fi

info "Re-applying configuration…"
bash "${SCRIPT_DIR}/apply.sh" --no-reconcile-runtime

info "Starting services…"
bash "${SCRIPT_DIR}/start.sh"

if easydeploy_quiet; then
	echo "Update complete."
else
	success "Update complete."
fi
