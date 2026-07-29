#!/usr/bin/env bash
# restore-bundle.sh — Restore OpenCloud on a fresh VPS from one backup archive
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

BUNDLE="${1:-}"

if [[ -z "$BUNDLE" || ! -f "$BUNDLE" ]]; then
	echo "Usage: bash restore-bundle.sh /path/to/opencloud-backup-*.tar.gz" >&2
	echo >&2
	echo "Fresh VPS quick start:" >&2
	echo "  git clone <repo> opencloud-easy-deploy && cd opencloud-easy-deploy" >&2
	echo "  bash restore-bundle.sh /path/to/backup.tar.gz" >&2
	echo >&2
	echo "Ensure DNS for your domains points at this server before apply finishes." >&2
	exit 1
fi

BUNDLE="$(readlink -f "$BUNDLE")"

echo
echo -e "${BOLD}OpenCloud Easy Deploy — restore from bundle${RESET}"
echo

info "Installing dependencies…"
bash "${SCRIPT_DIR}/ensure-dependencies.sh"

info "Restoring files from ${BUNDLE}…"
uv run python "${SCRIPT_DIR}/scripts/bundle.py" restore "$BUNDLE" --skip-apply

info "Starting OpenCloud stack…"
bash "${SCRIPT_DIR}/apply.sh"

echo
success "Restore complete."
echo
echo "  1. Confirm DNS for your OpenCloud and weboffice domains points at this server"
echo "  2. Open your OpenCloud URL (see domain in deploy.yaml)"
echo
warn "The backup file contains secrets — delete or encrypt it after restore if no longer needed."
