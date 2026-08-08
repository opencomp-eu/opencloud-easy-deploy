#!/usr/bin/env bash
# backup-bundle.sh — Create a single portable backup archive for VPS migration
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"
cd "${SCRIPT_DIR}"

if [[ -f "${SCRIPT_DIR}/deploy.yaml" ]] && docker inspect opencloud &>/dev/null 2>&1; then
	warn "OpenCloud is running — for a consistent backup, run: bash stop.sh"
fi

exec uv run python -m scripts.bundle create "$@"
