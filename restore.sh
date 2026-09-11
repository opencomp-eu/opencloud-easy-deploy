#!/usr/bin/env bash
# restore.sh — restore OpenCloud from a shared-lib Borg or portable archive.
# Legacy Borg archives and backup-bundle files remain supported when possible.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"
cd "${SCRIPT_DIR}"

if [[ -z "${EASYDEPLOY_BACKUP_PYTHON:-}" ]]; then
	if [[ -x "${SCRIPT_DIR}/.venv/bin/python" ]]; then
		EASYDEPLOY_BACKUP_PYTHON="${SCRIPT_DIR}/.venv/bin/python"
	else
		EASYDEPLOY_BACKUP_PYTHON="python3"
	fi
fi
export EASYDEPLOY_BACKUP_PYTHON

RESTORE_DIR="${SCRIPT_DIR}/.opencloud-easy-deploy/backup/restore"
PLAN_JSON=""

print_help() {
	cat <<EOF
Usage: bash restore.sh (--archive NAME | --latest | --file PATH | --list) [flags]

  --archive NAME       Restore a named Borg archive (full name or ID prefix)
  --latest             Restore the newest Borg archive
  --file PATH          Restore a portable archive (.tar.gz, .age, or .enc)
  --list               List archives in the configured Borg repository
  --dry-run            List a Borg archive without changing files
  --encrypt            Accepted for flag parity; encrypted inputs are detected
  --passphrase-file F  Passphrase file for openssl-encrypted portable files
  --yes                Skip restore confirmation prompts
  --keep-stopped       Do not start OpenCloud after restoring
  -h, --help           Show this help
EOF
}

usage_die() {
	print_help >&2
	die "$1"
}

require_command() {
	command -v "$1" &>/dev/null || die "Required command not found: $1"
}

load_plan_json() {
	PLAN_JSON="$(mktemp)"
	easydeploy_backup_py "${EASYDEPLOY_LIB}/python/backup_plan.py" \
		--project-root "${SCRIPT_DIR}" --emit-plan-json >"${PLAN_JSON}"
}

plan_hook() {
	"${EASYDEPLOY_BACKUP_PYTHON}" -c \
		'import json,sys; print(json.load(open(sys.argv[1])).get("hooks", {}).get(sys.argv[2], ""))' \
		"$1" "$2"
}

stack_running() {
	easydeploy_backup_container_running opencloud || \
		easydeploy_backup_container_running euro-office
}

cleanup() {
	rm -rf "${RESTORE_DIR}"
	[[ -n "${PLAN_JSON:-}" ]] && rm -f "${PLAN_JSON}"
}

legacy_bundle() {
	local path="$1"
	# MANIFEST.yaml identifies the original backup-bundle.py format. This check
	# deliberately ignores encrypted files, which require the new portable path.
	tar -tf "${path}" 2>/dev/null | grep -qx 'MANIFEST.yaml'
}

restore_legacy_bundle() {
	local path="$1"
	info "Restoring legacy OpenCloud bundle..."
	uv run python -m scripts.bundle restore "${path}" --skip-apply
	info "Reconciling restored configuration..."
	bash "${SCRIPT_DIR}/apply.sh"
}

restore_legacy_borg() {
	local archive="$1"
	info "Restoring legacy OpenCloud Borg archive..."
	uv run python -m scripts.backup restore --archive "${archive}"
}

main() {
	local mode="" archive="" file_path="" passphrase_file=""
	local yes="false" keep_stopped="false" dry_run="false"
	while (($#)); do
		case "$1" in
			--archive)
				[[ -n "${2:-}" ]] || usage_die "--archive requires a name"
				[[ -z "${mode}" ]] || usage_die "Borg archive sources are not both valid"
				mode="archive"; archive="$2"; shift ;;
			--latest)
				[[ -z "${mode}" ]] || usage_die "Borg archive sources are not both valid"
				mode="latest" ;;
			--file)
				[[ -n "${2:-}" ]] || usage_die "--file requires a value"
				[[ -z "${mode}" ]] || usage_die "Backup sources are not both valid"
				mode="file"; file_path="$2"; shift ;;
			--list)
				[[ -z "${mode}" ]] || usage_die "--list cannot be combined with a restore source"
				mode="list" ;;
			--dry-run) dry_run="true" ;;
			--encrypt) : ;;
			--passphrase-file)
				[[ -n "${2:-}" ]] || usage_die "--passphrase-file requires a path"
				passphrase_file="$2"; shift ;;
			--yes) yes="true" ;;
			--keep-stopped) keep_stopped="true" ;;
			-h|--help) print_help; return 0 ;;
			*) usage_die "Unknown argument: $1" ;;
		esac
		shift
	done
	[[ -n "${mode}" ]] || usage_die "Provide --archive NAME, --latest, --file PATH, or --list"
	[[ "${dry_run}" != "true" || "${mode}" == "archive" || "${mode}" == "latest" ]] || \
		usage_die "--dry-run is only supported for Borg archives"

	if [[ -n "${passphrase_file}" ]]; then
		[[ -f "${passphrase_file}" ]] || die "Passphrase file not found: ${passphrase_file}"
		export PASSPHRASE_FILE="${passphrase_file}"
	fi

	load_plan_json
	trap cleanup EXIT
	if [[ "${mode}" == "list" ]]; then
		require_command borg
		# The repository settings are intentionally loaded by the shared parser;
		# this also provides quoting-safe BORG_REPO/BORG_RSH exports.
		eval "$(easydeploy_backup_settings_shell "${SCRIPT_DIR}/deploy.yaml")"
		easydeploy_backup_repo_env "${SCRIPT_DIR}/.opencloud-easy-deploy/secrets.yaml"
		easydeploy_backup_list_archives "${BACKUP_REPO_URL}"
		return 0
	fi

	if [[ "${mode}" == "file" ]]; then
		[[ -f "${file_path}" ]] || die "Portable backup not found: ${file_path}"
	else
		require_command borg
		eval "$(easydeploy_backup_settings_shell "${SCRIPT_DIR}/deploy.yaml")"
		easydeploy_backup_repo_env "${SCRIPT_DIR}/.opencloud-easy-deploy/secrets.yaml"
	fi

	if [[ "${dry_run}" == "true" ]]; then
		local archive_name="${archive}"
		if [[ "${mode}" == "latest" ]]; then
			archive_name="$(borg list --short --last 1 "${BACKUP_REPO_URL}")"
		else
			archive_name="$(easydeploy_backup_resolve_archive "${BACKUP_REPO_URL}" "${archive}")"
		fi
		[[ -n "${archive_name}" ]] || die "No archive found."
		borg list "${BACKUP_REPO_URL}::${archive_name}"
		return 0
	fi

	if [[ "${yes}" != "true" ]]; then
		warn "Restoring overwrites deploy.yaml, secrets, and OpenCloud data directories."
		[[ -t 0 ]] || die "Refusing to restore non-interactively without --yes."
		local confirm=""
		ask_yn confirm "Restore this backup now?" n
		[[ "${confirm}" == "y" ]] || die "Aborted."
	fi

	local stop_hook start_hook stopped="false"
	stop_hook="$(plan_hook "${PLAN_JSON}" stop)"
	start_hook="$(plan_hook "${PLAN_JSON}" start)"
	if [[ -n "${stop_hook}" ]]; then
		if [[ "${mode}" == "file" ]]; then
			if stack_running; then
				info "Stopping OpenCloud..."
				easydeploy_backup_run_hook "${SCRIPT_DIR}" "${stop_hook}"
				stopped="true"
			fi
		else
			info "Stopping OpenCloud..."
			easydeploy_backup_run_hook "${SCRIPT_DIR}" "${stop_hook}"
			stopped="true"
		fi
	fi

	if [[ "${mode}" == "file" ]] && legacy_bundle "${file_path}"; then
		restore_legacy_bundle "${file_path}"
		rm -rf "${RESTORE_DIR}"
	elif [[ "${mode}" == "file" ]]; then
		mkdir -p "${RESTORE_DIR}"
		easydeploy_backup_extract_portable "${file_path}" "${RESTORE_DIR}"
		[[ -d "${RESTORE_DIR}/payload" ]] || die "Portable backup does not contain a payload directory."
		easydeploy_backup_restore_payload "${SCRIPT_DIR}" "${RESTORE_DIR}/payload" "${PLAN_JSON}"
	else
		local archive_name
		if [[ "${mode}" == "latest" ]]; then
			archive_name="$(borg list --short --last 1 "${BACKUP_REPO_URL}")"
			[[ -n "${archive_name}" ]] || die "No archives found in ${BACKUP_REPO_URL}."
		else
			archive_name="$(easydeploy_backup_resolve_archive "${BACKUP_REPO_URL}" "${archive}")"
		fi
		# Old OpenCloud archives contain backup-root/, while shared archives
		# contain payload/. Delegate the former to the tested legacy adapter.
		if borg list --short "${BACKUP_REPO_URL}::${archive_name}" backup-root >/dev/null 2>&1; then
			restore_legacy_borg "${archive_name}"
		else
			mkdir -p "${RESTORE_DIR}"
			info "Extracting Borg archive '${archive_name}'..."
			(cd "${RESTORE_DIR}" && borg extract "${BACKUP_REPO_URL}::${archive_name}")
			[[ -d "${RESTORE_DIR}/payload" ]] || die "Borg archive does not contain a payload directory."
			easydeploy_backup_restore_payload "${SCRIPT_DIR}" "${RESTORE_DIR}/payload" "${PLAN_JSON}"
		fi
	fi

	if [[ "${keep_stopped}" == "true" ]]; then
		warn "OpenCloud left stopped (--keep-stopped). Run bash start.sh later."
	elif [[ -n "${start_hook}" && ( "${stopped}" == "true" || "${mode}" != "file" ) ]]; then
		easydeploy_backup_run_hook "${SCRIPT_DIR}" "${start_hook}"
	fi
	success "Restore complete."
}

main "$@"
