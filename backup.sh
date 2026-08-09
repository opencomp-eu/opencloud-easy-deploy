#!/usr/bin/env bash
# backup.sh — Run a Borg backup now
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"
cd "${SCRIPT_DIR}"
exec uv run python -m scripts.backup run "$@"
