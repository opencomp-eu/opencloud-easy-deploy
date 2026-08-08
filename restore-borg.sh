#!/usr/bin/env bash
# restore-borg.sh — Restore OpenCloud on a fresh VPS from Borg (local or SFTP)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

echo
echo -e "${BOLD}OpenCloud Easy Deploy — restore from Borg${RESET}"
echo

if [[ -z "${BORG_PASSPHRASE:-}" ]]; then
	echo "Set BORG_PASSPHRASE (from your password manager) before running this script." >&2
	echo >&2
	echo "Example (SFTP off-site backups):" >&2
	echo "  export BORG_PASSPHRASE='your-borg-passphrase'" >&2
	echo "  export OCD_BACKUP_SFTP_HOST=backup.example.com" >&2
	echo "  export OCD_BACKUP_SFTP_USER=borg" >&2
	echo "  export OCD_BACKUP_SFTP_PATH=/repos/opencloud" >&2
	echo "  export OCD_BACKUP_SSH_KEY=/root/.ssh/borg_backup" >&2
	echo "  bash restore-borg.sh" >&2
	echo >&2
	echo "Example (local repository on this machine or mounted disk):" >&2
	echo "  export BORG_PASSPHRASE='your-borg-passphrase'" >&2
	echo "  export OCD_BACKUP_LOCAL_PATH=/var/backups/opencloud" >&2
	echo "  bash restore-borg.sh" >&2
	echo >&2
	echo "deploy.yaml and secrets.yaml are read from the Borg archive — nothing else to copy." >&2
	exit 1
fi

info "Installing dependencies…"
bash "${SCRIPT_DIR}/ensure-dependencies.sh"

info "Restoring from Borg repository…"
"${SCRIPT_DIR}/.venv/bin/python" "${SCRIPT_DIR}/scripts/backup.py" fresh-restore --latest "$@"

info "Starting OpenCloud stack…"
bash "${SCRIPT_DIR}/apply.sh"

echo
success "Restore complete."
echo
echo "  1. Confirm DNS for your OpenCloud and weboffice domains points at this server"
echo "  2. Open your OpenCloud URL (see domain in deploy.yaml)"
echo
warn "Keep BORG_PASSPHRASE and your SSH private key safe off-site — you need both for the next restore."
