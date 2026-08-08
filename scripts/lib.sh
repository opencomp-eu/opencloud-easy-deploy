#!/usr/bin/env bash
# scripts/lib.sh — shared utilities for opencloud-easy-deploy

_lib_sh_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_ocd_project_root="$(cd "${_lib_sh_dir}/.." && pwd)"

# shellcheck source=scripts/runtime_path.sh
source "${_lib_sh_dir}/runtime_path.sh"

# shellcheck source=easydeploy-lib/lib/init.sh
source "${_ocd_project_root}/easydeploy-lib/lib/init.sh"
