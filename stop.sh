#!/usr/bin/env bash
# stop.sh — stop Caddy and OpenCloud stack (data preserved)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

IFS=' ' read -ra DOCKER_COMPOSE <<< "$(docker_compose_cmd)"
COMPOSE_ENV="${SCRIPT_DIR}/opencloud-compose/.env"

if [[ -f "$COMPOSE_ENV" ]]; then
	load_deploy_env "$COMPOSE_ENV"
fi

info "Stopping Caddy…"
(cd "${SCRIPT_DIR}/caddy" && "${DOCKER_COMPOSE[@]}" down || true)

info "Stopping OpenCloud stack…"
(cd "${SCRIPT_DIR}/opencloud-compose" && "${DOCKER_COMPOSE[@]}" down --remove-orphans || true)

success "All services stopped. Data directories are unchanged."
