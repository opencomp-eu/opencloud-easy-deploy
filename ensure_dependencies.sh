#!/usr/bin/env bash
# ensure_dependencies.sh — verify host dependencies for opencloud-easy-deploy
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

if ! command -v uv &>/dev/null; then
	die "uv is required (https://docs.astral.sh/uv/getting-started/installation/)"
fi

info "Syncing Python dependencies with uv…"
uv sync --dev --directory "${SCRIPT_DIR}"

if ! command -v docker &>/dev/null; then
	warn "Docker is not installed."
	echo "  Install Docker Engine: https://docs.docker.com/engine/install/"
	exit 1
fi

if ! docker compose version &>/dev/null && ! command -v docker-compose &>/dev/null; then
	die "Docker Compose v2 is required"
fi

if ! command -v git &>/dev/null; then
	die "git is required for the opencloud-compose submodule"
fi

success "Dependencies look good."
