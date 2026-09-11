#!/usr/bin/env bash
# bootstrap-from-backup.sh — install dependencies and restore a portable archive
# on a fresh VPS. Both shared portable archives and legacy bundles are accepted.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

usage() {
	cat <<EOF
Usage: bash bootstrap-from-backup.sh PORTABLE_ARCHIVE [restore flags...]

Restores an archive produced by 'bash backup.sh --export PATH'. Legacy
backup-bundle.sh archives are also accepted. Extra flags are passed to
restore.sh (for example --yes, --passphrase-file FILE, --keep-stopped).
EOF
}

(($# >= 1)) || { usage >&2; die "A portable backup file is required."; }
if [[ "$1" == "-h" || "$1" == "--help" ]]; then
	usage
	exit 0
fi
[[ "$1" != -* ]] || { usage >&2; die "The first argument must be the portable backup file."; }
archive="$1"
shift
[[ -f "${archive}" ]] || die "Portable backup not found: ${archive}"

bash "${SCRIPT_DIR}/ensure-dependencies.sh"
exec bash "${SCRIPT_DIR}/restore.sh" --file "${archive}" "$@"
