#!/usr/bin/env bash
# diagnose.sh — check Euro Office / Caddy / OpenCloud connectivity
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

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

section "Euro Office readiness"
if docker inspect euro-office &>/dev/null; then
	status="$(docker inspect --format='{{.State.Health.Status}}' euro-office 2>/dev/null || echo unknown)"
	echo "  health status: ${status}"
	if docker exec euro-office bash -c 'curl -sf http://127.0.0.1/healthcheck | grep -q true' 2>/dev/null; then
		success "/healthcheck returns true"
	else
		warn "/healthcheck not ready yet — first boot can take several minutes"
		docker logs euro-office --since 10m 2>&1 | tail -15
	fi
else
	warn "euro-office container not running"
fi

section "Docker DNS from Caddy → Euro Office (internal)"
if docker inspect opencloud_caddy &>/dev/null; then
	if docker exec opencloud_caddy wget -qO- http://euro-office/hosting/discovery 2>/dev/null | head -5; then
		success "Caddy can reach http://euro-office/hosting/discovery"
	else
		error "Caddy cannot reach euro-office:80 — check container_name and opencloud-net"
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
info "If internal discovery works but errors persist, run: bash apply.sh"
