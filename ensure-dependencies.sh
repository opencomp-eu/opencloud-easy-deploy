#!/usr/bin/env bash
# ensure-dependencies.sh — install and verify host dependencies
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

export PATH="${HOME}/.local/bin:${PATH}"

run_as_root() {
	if [[ "${EUID}" -eq 0 ]]; then
		"$@"
	elif command -v sudo &>/dev/null; then
		sudo "$@"
	else
		die "Need root privileges for: $* — re-run with sudo or as root"
	fi
}

docker_usable() {
	command -v docker &>/dev/null || return 1
	docker info &>/dev/null 2>&1
}

compose_usable() {
	docker compose version &>/dev/null 2>&1 || command -v docker-compose &>/dev/null
}

ensure_git() {
	if command -v git &>/dev/null; then
		success "git present ($(git --version | head -1))"
		return
	fi
	info "Installing git…"
	if command -v apt-get &>/dev/null; then
		run_as_root apt-get update -qq
		run_as_root apt-get install -y git
	elif command -v dnf &>/dev/null; then
		run_as_root dnf install -y git
	elif command -v pacman &>/dev/null; then
		run_as_root pacman -Sy --noconfirm git
	else
		die "git is required but not installed — install git and re-run"
	fi
	success "git installed"
}

ensure_submodule() {
	if [[ ! -d "${SCRIPT_DIR}/.git" ]]; then
		warn "Not a git checkout — skipping submodule update"
		if [[ ! -f "${SCRIPT_DIR}/opencloud-compose/docker-compose.yml" ]]; then
			die "opencloud-compose is missing — clone the repo with submodules or copy opencloud-compose manually"
		fi
		return
	fi

	if [[ -f "${SCRIPT_DIR}/opencloud-compose/docker-compose.yml" ]]; then
		info "Updating opencloud-compose submodule…"
	else
		info "Initializing opencloud-compose submodule…"
	fi
	git -C "${SCRIPT_DIR}" submodule update --init --recursive
	success "opencloud-compose submodule ready"
}

ensure_docker() {
	if docker_usable; then
		success "Docker present ($(docker --version | head -1))"
	else
		if command -v docker &>/dev/null; then
			warn "Docker is installed but the daemon is not reachable — start docker and re-run"
			die "Try: sudo systemctl enable --now docker"
		fi
		info "Installing Docker (get.docker.com)…"
		curl -fsSL https://get.docker.com | run_as_root sh
		if ! docker_usable; then
			if [[ "${EUID}" -ne 0 ]] && ! groups | grep -q '\bdocker\b'; then
				warn "Your user is not in the docker group — log out/in after:"
				warn "  sudo usermod -aG docker ${USER}"
				warn "Or run docker commands with sudo until then."
			fi
			if ! docker_usable && [[ "${EUID}" -ne 0 ]]; then
				warn "Verifying Docker with sudo…"
				if sudo docker info &>/dev/null 2>&1; then
					success "Docker installed (use sudo docker until group membership applies)"
				else
					die "Docker install finished but daemon is not running"
				fi
			else
				die "Docker install finished but daemon is not running"
			fi
		else
			success "Docker installed"
		fi
	fi

	if compose_usable; then
		if docker compose version &>/dev/null 2>&1; then
			success "Docker Compose present ($(docker compose version --short 2>/dev/null || docker compose version | head -1))"
		else
			success "docker-compose present"
		fi
	else
		die "Docker Compose v2 is required — reinstall Docker or install the compose plugin"
	fi
}

ensure_uv() {
	if command -v uv &>/dev/null; then
		success "uv present ($(uv --version))"
		return
	fi
	info "Installing uv…"
	curl -LsSf https://astral.sh/uv/install.sh | sh
	export PATH="${HOME}/.local/bin:${PATH}"
	if ! command -v uv &>/dev/null; then
		die "uv install finished but uv is not on PATH — add ~/.local/bin to PATH"
	fi
	success "uv installed ($(uv --version))"
}

ensure_python_deps() {
	info "Syncing Python dependencies (uv sync --dev)…"
	uv sync --dev --directory "${SCRIPT_DIR}"
	success "Python dependencies ready"
}

main() {
	echo
	echo -e "${BOLD}OpenCloud Easy Deploy — ensure dependencies${RESET}"
	echo

	ensure_git
	ensure_submodule
	ensure_docker
	ensure_uv
	ensure_python_deps

	echo
	success "Host is ready. Next: bash wizard.sh  or  bash apply.sh"
	echo
}

main "$@"
