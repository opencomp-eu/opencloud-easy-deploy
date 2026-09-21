#!/usr/bin/env bash
# backup.sh — Borg and portable backups via easydeploy-lib.
# Existing backup-bundle.sh remains the legacy OpenCloud bundle format.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"
cd "${SCRIPT_DIR}"

# The shared helpers need PyYAML; prefer the kit environment when available.
if [[ -z "${EASYDEPLOY_BACKUP_PYTHON:-}" ]]; then
	if [[ -x "${SCRIPT_DIR}/.venv/bin/python" ]]; then
		EASYDEPLOY_BACKUP_PYTHON="${SCRIPT_DIR}/.venv/bin/python"
	else
		EASYDEPLOY_BACKUP_PYTHON="python3"
	fi
fi
export EASYDEPLOY_BACKUP_PYTHON

BACKUP_STATE_DIR="${SCRIPT_DIR}/.opencloud-easy-deploy/backup"
BACKUP_STAGING_CURRENT="${BACKUP_STATE_DIR}/staging/current"
PLAN_JSON=""
STACK_STOPPED="false"

print_help() {
	cat <<EOF
Usage: bash backup.sh [flags]

  (no flags)            Create a Borg archive from the dynamic backup plan
  --list                List archives in the configured Borg repository
  --export PATH         Also write a portable tar.gz after a successful backup
  --export-only PATH    Write a portable archive without updating Borg
  --export-from-archive NAME --export PATH
                        Export an existing Borg archive to portable format
  --encrypt             Encrypt portable output (age prompt or passphrase env)
  --cold                Stop OpenCloud before staging, then restart it
  --schedule            Install/remove the systemd timer from deploy.yaml
  -h, --help            Show this help
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

plan_field() {
	"${EASYDEPLOY_BACKUP_PYTHON}" -c \
		'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' \
		"$1" "$2"
}

plan_hook() {
	"${EASYDEPLOY_BACKUP_PYTHON}" -c \
		'import json,sys; print(json.load(open(sys.argv[1])).get("hooks", {}).get(sys.argv[2], ""))' \
		"$1" "$2"
}

load_settings() {
	[[ -f "${SCRIPT_DIR}/deploy.yaml" ]] || \
		die "Missing deploy.yaml — copy deploy.yaml.example and run bash apply.sh first."
	eval "$(easydeploy_backup_settings_shell "${SCRIPT_DIR}/deploy.yaml")"
}

require_backup_enabled() {
	[[ "${BACKUP_ENABLED:-false}" == "true" ]] || \
		die "Backups are disabled. Set backup.enabled=true in deploy.yaml."
}

cleanup() {
	local rc=$? start_hook=""
	if [[ "${STACK_STOPPED}" == "true" && -n "${PLAN_JSON:-}" ]]; then
		start_hook="$(plan_hook "${PLAN_JSON}" start)"
	fi
	rm -rf "${BACKUP_STAGING_CURRENT}" 2>/dev/null || true
	[[ -n "${PLAN_JSON:-}" ]] && rm -f "${PLAN_JSON}" 2>/dev/null || true
	if [[ "${STACK_STOPPED}" == "true" ]]; then
		warn "Restarting OpenCloud after backup cleanup."
		easydeploy_backup_run_hook "${SCRIPT_DIR}" "${start_hook}" || \
			warn "Automatic restart failed; run bash start.sh manually."
	fi
	exit "${rc}"
}

run_backup() {
	local stop_hook start_hook archive_prefix
	stop_hook="$(plan_hook "${PLAN_JSON}" stop)"
	start_hook="$(plan_hook "${PLAN_JSON}" start)"
	archive_prefix="$(plan_field "${PLAN_JSON}" archive_prefix)"

	if [[ "${COLD}" == "true" ]]; then
		[[ -n "${stop_hook}" ]] || die "--cold requires a stop hook in the backup plan"
		info "Stopping OpenCloud for a consistent backup..."
		easydeploy_backup_run_hook "${SCRIPT_DIR}" "${stop_hook}"
		STACK_STOPPED="true"
	fi

	info "Staging backup payload..."
	stage_payload "${SCRIPT_DIR}" "${BACKUP_STAGING_CURRENT}" "${BACKUP_REPO_URL:-}" "false"

	if [[ "${EXPORT_ONLY}" == "true" ]]; then
		easydeploy_backup_export_portable "${EXPORT_PATH}" "${BACKUP_STAGING_CURRENT}" "${ENCRYPT_EXPORT}"
		return 0
	fi

	local borgmatic_config="${BACKUP_STATE_DIR}/borgmatic.yaml"
	mkdir -p "${BACKUP_STATE_DIR}"
	easydeploy_backup_write_borgmatic_config "${borgmatic_config}" "${BACKUP_REPO_URL}" \
		"${BACKUP_STAGING_CURRENT}" "${archive_prefix}"
	easydeploy_backup_repo_create "${borgmatic_config}"
	info "Creating Borg archive (prefix: ${archive_prefix})..."
	borgmatic --config "${borgmatic_config}" create --stats
	borgmatic --config "${borgmatic_config}" prune --stats
	borgmatic --config "${borgmatic_config}" check

	if [[ -n "${EXPORT_PATH}" ]]; then
		easydeploy_backup_export_portable "${EXPORT_PATH}" "${BACKUP_STAGING_CURRENT}" "${ENCRYPT_EXPORT}"
	fi
	if [[ "${STACK_STOPPED}" == "true" ]]; then
		info "Restarting OpenCloud after cold backup..."
		easydeploy_backup_run_hook "${SCRIPT_DIR}" "${start_hook}"
		STACK_STOPPED="false"
	fi
	success "Backup complete."
}

stage_payload() {
	# The pinned shared helper installs a RETURN trap that references its
	# function-local plan_json after the function returns. Keep nounset off
	# while that trap is evaluated in this kit's strict-mode entrypoint.
	set +u
	easydeploy_backup_stage_payload "$@"
	local rc=$?
	trap - RETURN
	set -u
	return "${rc}"
}

main() {
	local list_only="false" schedule="false"
	EXPORT_PATH="" EXPORT_ONLY="false" EXPORT_FROM_ARCHIVE="" ENCRYPT_EXPORT="false" COLD="false"
	while (($#)); do
		case "$1" in
			--list) list_only="true" ;;
			--export)
				[[ -n "${2:-}" ]] || usage_die "--export requires a path"
				EXPORT_PATH="$2"; shift ;;
			--export-only)
				[[ -n "${2:-}" ]] || usage_die "--export-only requires a path"
				EXPORT_ONLY="true"; EXPORT_PATH="$2"; shift ;;
			--export-from-archive)
				[[ -n "${2:-}" ]] || usage_die "--export-from-archive requires an archive name"
				EXPORT_FROM_ARCHIVE="$2"; shift ;;
			--encrypt) ENCRYPT_EXPORT="true" ;;
			--cold) COLD="true" ;;
			--schedule) schedule="true" ;;
			-h|--help) print_help; return 0 ;;
			*) usage_die "Unknown argument: $1" ;;
		esac
		shift
	done
	[[ -z "${EXPORT_FROM_ARCHIVE}" || -n "${EXPORT_PATH}" ]] || \
		usage_die "--export-from-archive requires --export PATH"

	if [[ "${schedule}" == "true" ]]; then
		load_plan_json
		load_settings
		"${EASYDEPLOY_BACKUP_PYTHON}" "${EASYDEPLOY_LIB}/python/backup_schedule.py" \
			--project-root "${SCRIPT_DIR}" --deploy-yaml "${SCRIPT_DIR}/deploy.yaml" \
			--unit-name "$(plan_field "${PLAN_JSON}" timer_name)"
		rm -f "${PLAN_JSON}"
		PLAN_JSON=""
		return 0
	fi

	load_plan_json
	load_settings
	if [[ "${list_only}" == "true" ]]; then
		require_command borg
		require_backup_enabled
		easydeploy_backup_repo_env "${SCRIPT_DIR}/.opencloud-easy-deploy/secrets.yaml"
		easydeploy_backup_list_archives "${BACKUP_REPO_URL}"
		return 0
	fi

	if [[ -n "${EXPORT_FROM_ARCHIVE}" ]]; then
		require_command borg
		require_backup_enabled
		easydeploy_backup_repo_env "${SCRIPT_DIR}/.opencloud-easy-deploy/secrets.yaml"
		local resolved
		resolved="$(easydeploy_backup_resolve_archive "${BACKUP_REPO_URL}" "${EXPORT_FROM_ARCHIVE}")"
		easydeploy_backup_export_from_archive "${BACKUP_REPO_URL}" "${resolved}" "${EXPORT_PATH}" "${ENCRYPT_EXPORT}"
		return 0
	fi

	if [[ "${EXPORT_ONLY}" == "true" ]]; then
		[[ -n "${EXPORT_PATH}" ]] || usage_die "--export-only requires --export PATH"
		trap cleanup EXIT
		run_backup
		return 0
	fi

	require_backup_enabled
	require_command borg
	require_command borgmatic
	easydeploy_backup_repo_env "${SCRIPT_DIR}/.opencloud-easy-deploy/secrets.yaml"
	trap cleanup EXIT
	run_backup
}

main "$@"
