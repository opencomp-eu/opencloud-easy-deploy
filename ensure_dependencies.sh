#!/usr/bin/env bash
# ensure_dependencies.sh — backward-compatible alias for ensure-dependencies.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${SCRIPT_DIR}/ensure-dependencies.sh" "$@"
