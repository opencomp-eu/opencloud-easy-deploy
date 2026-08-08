#!/usr/bin/env bash
# restore.sh — List or restore Borg backups
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${SCRIPT_DIR}/.venv/bin/python"

if [[ "${1:-}" == "--list" ]]; then
	shift
	exec "$PYTHON" "${SCRIPT_DIR}/scripts/backup.py" list "$@"
fi

if [[ "${1:-}" == "--latest" ]]; then
	shift
	exec "$PYTHON" "${SCRIPT_DIR}/scripts/backup.py" restore --latest "$@"
fi

if [[ "${1:-}" == "--archive" ]]; then
	shift
	archive="${1:-}"
	[[ -n "$archive" ]] || { echo "Usage: restore.sh --archive NAME [--dry-run]" >&2; exit 1; }
	shift
	exec "$PYTHON" "${SCRIPT_DIR}/scripts/backup.py" restore --archive "$archive" "$@"
fi

if [[ "${1:-}" == "--dry-run" ]]; then
	shift
	exec "$PYTHON" "${SCRIPT_DIR}/scripts/backup.py" restore --dry-run "$@"
fi

cat <<EOF
OpenCloud Borg restore

Usage:
  bash restore.sh --list
  bash restore.sh --latest              Restore newest archive (stop stack first: bash stop.sh)
  bash restore.sh --archive NAME        Restore a specific archive
  bash restore.sh --archive NAME --dry-run

After restore:
  bash apply.sh && bash start.sh
EOF

exec "$PYTHON" "${SCRIPT_DIR}/scripts/backup.py" restore "$@"
