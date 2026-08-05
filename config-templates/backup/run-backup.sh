#!/bin/sh
# Borg backup runner — executed inside the backup container.
set -eu

REPO="${BORG_REPO:-/repo}"
PREFIX="${BORG_ARCHIVE_PREFIX:-opencloud}"
ARCHIVE="${PREFIX}-$(date -u +%Y%m%dT%H%M%SZ)"

if ! borg info "${REPO}" >/dev/null 2>&1; then
	echo "Initializing Borg repository at ${REPO}…"
	borg init --encryption=repokey-blake2 "${REPO}"
fi

echo "Creating archive ${ARCHIVE}…"
borg create --stats --compression lz4 "${REPO}::${ARCHIVE}" /backup-root

echo "Applying retention policy…"
borg prune -v "${REPO}" \
	--keep-daily="${KEEP_DAILY:-7}" \
	--keep-weekly="${KEEP_WEEKLY:-4}" \
	--keep-monthly="${KEEP_MONTHLY:-6}" \
	--keep-yearly="${KEEP_YEARLY:-0}"

borg compact "${REPO}"
echo "Backup complete: ${ARCHIVE}"
