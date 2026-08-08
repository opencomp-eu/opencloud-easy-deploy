#!/usr/bin/env bash
# apply.sh — Apply configuration from deploy.yaml
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/runtime_path.sh
source "${SCRIPT_DIR}/scripts/runtime_path.sh"

ensure_dependencies="false"
python_args=()

for arg in "$@"; do
	case "$arg" in
		--ensure-dependencies)
			ensure_dependencies="true"
			;;
		*)
			python_args+=("$arg")
			;;
	esac
done

if [[ "$ensure_dependencies" == "true" ]]; then
	bash "${SCRIPT_DIR}/ensure-dependencies.sh"
fi

uv run python "${SCRIPT_DIR}/scripts/apply.py" "${python_args[@]}"
