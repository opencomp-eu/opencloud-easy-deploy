#!/usr/bin/env bash
# backup.sh — Run a Borg backup now
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/runtime_path.sh
source "${SCRIPT_DIR}/scripts/runtime_path.sh"
exec uv run python "${SCRIPT_DIR}/scripts/backup.py" run "$@"
