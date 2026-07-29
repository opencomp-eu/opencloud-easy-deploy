#!/usr/bin/env bash
# start.sh — start OpenCloud stack (includes Caddy)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

IFS=' ' read -ra DOCKER_COMPOSE <<< "$(docker_compose_cmd)"
COMPOSE_ENV="${SCRIPT_DIR}/opencloud-compose/.env"

if [[ -f "$COMPOSE_ENV" ]]; then
	load_deploy_env "$COMPOSE_ENV"
fi

# Tear down legacy standalone Caddy stack if present.
if [[ -f "${SCRIPT_DIR}/caddy/docker-compose.yml" ]]; then
	(cd "${SCRIPT_DIR}/caddy" && "${DOCKER_COMPOSE[@]}" down 2>/dev/null || true)
fi

info "Starting OpenCloud stack…"
(cd "${SCRIPT_DIR}/opencloud-compose" && "${DOCKER_COMPOSE[@]}" up -d)

if docker inspect euro-office &>/dev/null; then
	info "Waiting for Euro Office WOPI discovery…"
	for _ in $(seq 1 60); do
		if docker exec euro-office bash -c 'exec 3<>/dev/tcp/127.0.0.1/80 && printf "GET /hosting/discovery HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n" >&3 && cat <&3 | head -1 | grep -q "200 OK"' 2>/dev/null; then
			break
		fi
		sleep 10
	done
fi

if docker inspect opencloud &>/dev/null; then
	info "Restarting OpenCloud to refresh WOPI discovery…"
	docker restart opencloud >/dev/null
fi

success "All services started."
