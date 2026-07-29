#!/usr/bin/env bash
# scripts/lib.sh — shared utilities for opencloud-easy-deploy

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

info()    { echo -e "${CYAN}  -->${RESET} $*"; }
success() { echo -e "${GREEN}  [ok]${RESET} $*"; }
warn()    { echo -e "${YELLOW}  [!]${RESET}  $*"; }
error()   { echo -e "${RED}  [ERR]${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }

load_deploy_env() {
	local deploy_env="$1"
	[[ -f "$deploy_env" ]] || return 0

	while IFS='=' read -r key value || [[ -n "$key" ]]; do
		[[ -z "$key" || "$key" == \#* ]] && continue
		[[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
		value="${value%%#*}"
		value="${value%"${value##*[![:space:]]}"}"
		export "${key}=${value}"
	done < "$deploy_env"
}

ask() {
	local _var="$1"
	local _prompt="$2"
	local _default="${3:-}"

	if [[ -n "$_default" ]]; then
		echo -ne "${BOLD}  ${_prompt}${RESET} ${CYAN}[${_default}]${RESET}: "
	else
		echo -ne "${BOLD}  ${_prompt}${RESET}: "
	fi

	local _input
	read -r _input
	printf -v "$_var" '%s' "${_input:-$_default}"
}

ask_secret() {
	local _var="$1"
	local _prompt="$2"

	echo -ne "${BOLD}  ${_prompt}${RESET}: "
	local _input
	read -rs _input
	echo
	printf -v "$_var" '%s' "$_input"
}

ask_yn() {
	local _var="$1"
	local _prompt="$2"
	local _default="${3:-n}"

	local suffix="y/N"
	[[ "$_default" == "y" ]] && suffix="Y/n"

	echo -ne "${BOLD}  ${_prompt}${RESET} ${CYAN}[${suffix}]${RESET}: "
	local _input
	read -r _input
	_input="${_input:-$_default}"
	case "${_input,,}" in
		y|yes) printf -v "$_var" '%s' "y" ;;
		*) printf -v "$_var" '%s' "n" ;;
	esac
}

docker_compose_cmd() {
	if docker compose version &>/dev/null; then
		echo "docker compose"
		return 0
	fi
	if command -v docker-compose &>/dev/null; then
		echo "docker-compose"
		return 0
	fi
	die "Docker Compose v2 is required (docker compose)"
}

ensure_docker_network() {
	local network_name="$1"
	if ! docker network inspect "$network_name" &>/dev/null; then
		info "Creating Docker network ${network_name}…"
		docker network create "$network_name"
	fi
}

base_domain_from_host() {
	local host="$1"
	local parts=(${host//./ })
	local count=${#parts[@]}
	if (( count >= 2 )); then
		echo "${parts[count-2]}.${parts[count-1]}"
	else
		echo "$host"
	fi
}
