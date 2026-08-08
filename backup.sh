#!/usr/bin/env bash
# backup.sh — Run a Borg backup now
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/.venv/bin/python" "${SCRIPT_DIR}/scripts/backup.py" run "$@"
