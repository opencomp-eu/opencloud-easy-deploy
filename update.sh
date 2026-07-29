#!/usr/bin/env bash
# update.sh — pull latest opencloud-compose and container images
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

info "Updating opencloud-compose submodule…"
if [[ -d "${SCRIPT_DIR}/.git" ]]; then
	git -C "${SCRIPT_DIR}" submodule update --remote --merge opencloud-compose || \
		warn "Submodule update failed; continuing with existing checkout."
fi

info "Stopping services…"
bash "${SCRIPT_DIR}/stop.sh"

IFS=' ' read -ra DOCKER_COMPOSE <<< "$(docker_compose_cmd)"
COMPOSE_ENV="${SCRIPT_DIR}/opencloud-compose/.env"

if [[ -f "$COMPOSE_ENV" ]]; then
	load_deploy_env "$COMPOSE_ENV"
fi

info "Pulling updated images…"
(cd "${SCRIPT_DIR}/opencloud-compose" && "${DOCKER_COMPOSE[@]}" pull)
docker pull caddy:2-alpine

info "Re-applying configuration…"
bash "${SCRIPT_DIR}/apply.sh" --no-reconcile-runtime

info "Starting services…"
bash "${SCRIPT_DIR}/start.sh"

success "Update complete."
