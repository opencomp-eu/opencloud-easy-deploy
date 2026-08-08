#!/usr/bin/env bash
# diagnose.sh — check Euro Office / Caddy / OpenCloud connectivity
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"
cd "${SCRIPT_DIR}"

COMPOSE_ENV="${SCRIPT_DIR}/opencloud-compose/.env"
DEPLOY_YAML="${SCRIPT_DIR}/deploy.yaml"

if [[ -f "$COMPOSE_ENV" ]]; then
	load_deploy_env "$COMPOSE_ENV"
fi

OC_DOMAIN="${OC_DOMAIN:-}"
EURO_DOMAIN="${EURO_OFFICE_DOMAIN:-}"

if [[ -z "$OC_DOMAIN" && -f "$DEPLOY_YAML" ]]; then
	OC_DOMAIN="$(uv run python - <<PY 2>/dev/null || true
import yaml
from pathlib import Path
data = yaml.safe_load(Path("${DEPLOY_YAML}").read_text()) or {}
print(data.get("opencloud", {}).get("domain", ""))
PY
)"
fi

if [[ -z "$EURO_DOMAIN" && -f "$DEPLOY_YAML" ]]; then
	EURO_DOMAIN="$(uv run python - <<PY 2>/dev/null || true
import yaml
from pathlib import Path
data = yaml.safe_load(Path("${DEPLOY_YAML}").read_text()) or {}
print(data.get("weboffice", {}).get("domain", ""))
PY
)"
fi

section() {
	echo
	echo -e "${BOLD}== $*${RESET}"
}

http_code() {
	curl -k -sS -o /dev/null -w "%{http_code}" "$1" 2>/dev/null || echo "000"
}

section "Containers"
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -E 'opencloud|caddy|euro-office|NAMES' || true

section "Euro Office WOPI discovery (internal)"
if docker inspect euro-office &>/dev/null; then
	status="$(docker inspect --format='{{.State.Health.Status}}' euro-office 2>/dev/null || echo unknown)"
	echo "  health status: ${status}"
	if docker exec euro-office bash -c 'exec 3<>/dev/tcp/127.0.0.1/80 && printf "GET /hosting/discovery HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n" >&3 && cat <&3 | head -1 | grep -q "200 OK"'; then
		success "WOPI /hosting/discovery returns 200 (Host: localhost)"
	else
		warn "WOPI discovery not ready — docservice may still be starting"
		docker logs euro-office --since 10m 2>&1 | tail -15
	fi
else
	warn "euro-office container not running"
fi

if [[ -n "$EURO_DOMAIN" ]] && docker inspect opencloud &>/dev/null; then
	section "OpenCloud → Euro Office discovery (public URL)"
	code="$(docker exec opencloud wget -q -S -O /dev/null "https://${EURO_DOMAIN}/hosting/discovery" 2>&1 | awk '/HTTP\// {print $2; exit}' || echo "000")"
	echo "  https://${EURO_DOMAIN}/hosting/discovery → HTTP ${code}"
	if [[ "$code" == "200" ]]; then
		success "OpenCloud can fetch public WOPI discovery"
	else
		warn "OpenCloud cannot reach public WOPI discovery (check host-gateway + Caddy)"
	fi
fi

section "Docker DNS from Caddy → backends (same compose network)"
if docker inspect opencloud_caddy &>/dev/null; then
	if docker exec opencloud_caddy wget -qO- http://opencloud:9200/ 2>/dev/null | head -1; then
		success "Caddy can reach http://opencloud:9200"
	else
		error "Caddy cannot reach opencloud:9200"
	fi
	if docker exec opencloud_caddy wget -qO- http://euro-office/hosting/discovery 2>/dev/null | head -3; then
		success "Caddy can reach http://euro-office/hosting/discovery"
	else
		error "Caddy cannot reach euro-office:80"
	fi
else
	warn "Caddy container opencloud_caddy not running"
fi

section "Docker DNS from OpenCloud → Euro Office (internal)"
if docker inspect opencloud &>/dev/null; then
	if docker exec opencloud wget -qO- http://euro-office/hosting/discovery 2>/dev/null | head -5; then
		success "OpenCloud can reach http://euro-office/hosting/discovery"
	else
		error "OpenCloud cannot reach euro-office:80"
	fi
else
	warn "OpenCloud container not running"
fi

if [[ -n "$EURO_DOMAIN" ]]; then
	section "Euro Office iframe headers (must allow ${OC_DOMAIN:-OpenCloud})"
	headers="$(curl -k -sSI "https://${EURO_DOMAIN}/hosting/discovery" 2>/dev/null || true)"
	echo "$headers" | grep -iE '^(x-frame-options|content-security-policy):' || echo "  (no frame headers)"
	if echo "$headers" | grep -qi 'x-frame-options:.*sameorigin'; then
		error "X-Frame-Options SAMEORIGIN blocks embedding in OpenCloud — re-run apply.sh"
	elif echo "$headers" | grep -qi "frame-ancestors.*${OC_DOMAIN}"; then
		success "CSP frame-ancestors allows OpenCloud"
	else
		warn "Missing frame-ancestors for OpenCloud — document editor iframe will be blocked"
	fi
fi

if docker inspect opencloud &>/dev/null; then
	section "OpenCloud collaboration secrets (env)"
	docker exec opencloud env 2>/dev/null | grep -E '^COLLABORATION_(WOPI|JWT)_SECRET=' || warn "COLLABORATION_* secrets not set in container env"
	if docker exec opencloud env 2>/dev/null | grep -q '^COLLABORATION_JWT_SECRET='; then
		warn "COLLABORATION_JWT_SECRET overrides OC_JWT_SECRET and can break WOPI tokens — should be unset"
	fi
fi

if docker inspect euro-office &>/dev/null; then
	section "Euro Office JWT (env)"
	if docker exec euro-office printenv JWT_SECRET 2>/dev/null | grep -q .; then
		success "JWT_SECRET is set via container environment"
	else
		warn "JWT_SECRET not set — document editing will fail"
	fi
fi

if [[ -n "$EURO_DOMAIN" ]]; then
	section "Public HTTPS: Euro Office discovery"
	code="$(http_code "https://${EURO_DOMAIN}/hosting/discovery")"
	echo "  https://${EURO_DOMAIN}/hosting/discovery → HTTP ${code}"
	if [[ "$code" == "200" ]]; then
		success "Public Euro Office discovery OK"
	else
		warn "Public discovery failed — browser editing may still work if internal discovery succeeds"
	fi
fi

if [[ -n "$OC_DOMAIN" ]]; then
	section "Public HTTPS: OpenCloud"
	code="$(http_code "https://${OC_DOMAIN}/")"
	echo "  https://${OC_DOMAIN}/ → HTTP ${code}"
fi

section "Recent OpenCloud collaboration errors"
docker logs opencloud --since 5m 2>&1 | grep -E 'WopiDiscovery|collaboration|502' | tail -10 || echo "  (none)"

	echo
	info "If WOPI errors are old but Euro Office is ready now: docker restart opencloud"
