#!/usr/bin/env bash
# start.sh — start OpenCloud stack and Caddy
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

IFS=' ' read -ra DOCKER_COMPOSE <<< "$(docker_compose_cmd)"
COMPOSE_ENV="${SCRIPT_DIR}/opencloud-compose/.env"

ensure_docker_network "opencloud-net"

if [[ -f "$COMPOSE_ENV" ]]; then
	load_deploy_env "$COMPOSE_ENV"
fi

info "Starting Caddy…"
(cd "${SCRIPT_DIR}/caddy" && "${DOCKER_COMPOSE[@]}" up -d)

info "Starting OpenCloud stack…"
(cd "${SCRIPT_DIR}/opencloud-compose" && "${DOCKER_COMPOSE[@]}" up -d)

if docker inspect opencloud &>/dev/null; then
	info "Restarting OpenCloud to refresh WOPI discovery…"
	docker restart opencloud >/dev/null
fi

success "All services started."
