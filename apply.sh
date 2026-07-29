#!/usr/bin/env bash
# apply.sh — Apply configuration from deploy.yaml
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
	bash "${SCRIPT_DIR}/ensure_dependencies.sh"
fi

uv run python "${SCRIPT_DIR}/scripts/apply.py" "${python_args[@]}"
