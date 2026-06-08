#!/usr/bin/env bash
#
# deploy.sh - Manage DeerFlow production Docker services.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DOCKER_DIR="$REPO_ROOT/docker"
COMPOSE_CMD=(docker compose -p deer-flow -f "$DOCKER_DIR/docker-compose.yaml")

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

usage() {
    cat <<'EOF'
Usage: scripts/deploy.sh [command] [options]

Commands:
  up                Build images and start production services (default)
  build             Build production images
  start             Start pre-built production services
  restart           Restart production services without rebuilding
  down              Stop and remove production containers
  status            Show production container status
  logs [service]    Tail logs for all services or one service
  config            Show resolved deployment configuration
  help              Show this help message

Services for logs:
  nginx, frontend, gateway, provisioner

Environment:
  PORT                              Public nginx port, default 2026
  DEER_FLOW_HOME                    Runtime data directory
  DEER_FLOW_CONFIG_PATH             Path to config.yaml
  DEER_FLOW_EXTENSIONS_CONFIG_PATH  Path to extensions_config.json
  DEER_FLOW_DOCKER_SOCKET          Docker socket path, default /var/run/docker.sock
  BETTER_AUTH_SECRET                Frontend auth/session secret
  UV_INDEX_URL, NPM_REGISTRY        Optional package registry mirrors

Examples:
  scripts/deploy.sh
  scripts/deploy.sh build
  scripts/deploy.sh start
  scripts/deploy.sh logs gateway
  scripts/deploy.sh status
  scripts/deploy.sh down
EOF
}

error() {
    echo -e "${RED}ERROR:${NC} $*" >&2
}

info() {
    echo -e "${BLUE}$*${NC}"
}

ok() {
    echo -e "${GREEN}$*${NC}"
}

warn() {
    echo -e "${YELLOW}$*${NC}"
}

set_default_env() {
    if [ -z "${DEER_FLOW_HOME:-}" ]; then
        export DEER_FLOW_HOME="$REPO_ROOT/backend/.deer-flow"
    fi

    export DEER_FLOW_REPO_ROOT="${DEER_FLOW_REPO_ROOT:-$REPO_ROOT}"
    export DEER_FLOW_CONFIG_PATH="${DEER_FLOW_CONFIG_PATH:-$REPO_ROOT/config.yaml}"
    export DEER_FLOW_EXTENSIONS_CONFIG_PATH="${DEER_FLOW_EXTENSIONS_CONFIG_PATH:-$REPO_ROOT/extensions_config.json}"
    export DEER_FLOW_DOCKER_SOCKET="${DEER_FLOW_DOCKER_SOCKET:-/var/run/docker.sock}"
}

docker_available() {
    command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

compose_available() {
    docker compose version >/dev/null 2>&1
}

require_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        error "Docker CLI is not installed or not on PATH."
        echo "Install Docker, start the daemon, then retry."
        exit 1
    fi

    if ! docker info >/dev/null 2>&1; then
        error "Docker daemon is not reachable."
        echo "Start Docker and verify with: docker info"
        exit 1
    fi

    if ! compose_available; then
        error "Docker Compose plugin is not available."
        echo "Install Docker Compose v2 and verify with: docker compose version"
        exit 1
    fi
}

ensure_config_file() {
    if [ -f "$DEER_FLOW_CONFIG_PATH" ]; then
        ok "config.yaml: $DEER_FLOW_CONFIG_PATH"
        return
    fi

    if [ -f "$REPO_ROOT/config.example.yaml" ]; then
        mkdir -p "$(dirname "$DEER_FLOW_CONFIG_PATH")"
        cp "$REPO_ROOT/config.example.yaml" "$DEER_FLOW_CONFIG_PATH"
        ok "Seeded config.example.yaml -> $DEER_FLOW_CONFIG_PATH"
        warn "config.yaml was seeded from the example template."
        echo "Edit $DEER_FLOW_CONFIG_PATH and set the required model credentials before serving users."
        return
    fi

    error "No config.yaml found at $DEER_FLOW_CONFIG_PATH."
    echo "Run 'make setup' or 'make config', then set the required model credentials."
    exit 1
}

ensure_extensions_config_file() {
    if [ -f "$DEER_FLOW_EXTENSIONS_CONFIG_PATH" ]; then
        ok "extensions_config.json: $DEER_FLOW_EXTENSIONS_CONFIG_PATH"
        return
    fi

    mkdir -p "$(dirname "$DEER_FLOW_EXTENSIONS_CONFIG_PATH")"
    if [ -f "$REPO_ROOT/extensions_config.example.json" ]; then
        cp "$REPO_ROOT/extensions_config.example.json" "$DEER_FLOW_EXTENSIONS_CONFIG_PATH"
        ok "Seeded extensions_config.example.json -> $DEER_FLOW_EXTENSIONS_CONFIG_PATH"
    else
        printf '{"mcpServers":{},"skills":{}}\n' > "$DEER_FLOW_EXTENSIONS_CONFIG_PATH"
        warn "extensions_config.json not found; created an empty config at $DEER_FLOW_EXTENSIONS_CONFIG_PATH"
    fi
}

generate_secret() {
    if command -v python3 >/dev/null 2>&1; then
        python3 -c 'import secrets; print(secrets.token_hex(32))'
    elif command -v python >/dev/null 2>&1; then
        python -c 'import secrets; print(secrets.token_hex(32))'
    elif command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32
    else
        return 1
    fi
}

ensure_better_auth_secret() {
    local secret_file="$DEER_FLOW_HOME/.better-auth-secret"

    if [ -n "${BETTER_AUTH_SECRET:-}" ]; then
        ok "BETTER_AUTH_SECRET: set from environment"
        return
    fi

    if [ -f "$secret_file" ]; then
        export BETTER_AUTH_SECRET
        BETTER_AUTH_SECRET="$(cat "$secret_file")"
        ok "BETTER_AUTH_SECRET: loaded from $secret_file"
        return
    fi

    export BETTER_AUTH_SECRET
    if ! BETTER_AUTH_SECRET="$(generate_secret)"; then
        error "Cannot generate BETTER_AUTH_SECRET: python3, python, and openssl are unavailable."
        echo "Set BETTER_AUTH_SECRET manually before running production Docker commands."
        exit 1
    fi

    printf '%s\n' "$BETTER_AUTH_SECRET" > "$secret_file"
    chmod 600 "$secret_file"
    ok "BETTER_AUTH_SECRET: generated and saved to $secret_file"
}

ensure_runtime_files() {
    set_default_env

    info "DEER_FLOW_HOME=$DEER_FLOW_HOME"
    mkdir -p "$DEER_FLOW_HOME"

    ensure_config_file
    ensure_extensions_config_file
    ensure_better_auth_secret
}

ensure_down_env() {
    set_default_env
    export BETTER_AUTH_SECRET="${BETTER_AUTH_SECRET:-placeholder}"
}

detect_sandbox_mode() {
    local sandbox_use=""
    local provisioner_url=""

    set_default_env
    [ -f "$DEER_FLOW_CONFIG_PATH" ] || { echo "local"; return; }

    sandbox_use=$(awk '
        /^[[:space:]]*sandbox:[[:space:]]*$/ { in_sandbox=1; next }
        in_sandbox && /^[^[:space:]#]/ { in_sandbox=0 }
        in_sandbox && /^[[:space:]]*use:[[:space:]]*/ {
            line=$0
            sub(/^[[:space:]]*use:[[:space:]]*/, "", line)
            print line
            exit
        }
    ' "$DEER_FLOW_CONFIG_PATH")

    provisioner_url=$(awk '
        /^[[:space:]]*sandbox:[[:space:]]*$/ { in_sandbox=1; next }
        in_sandbox && /^[^[:space:]#]/ { in_sandbox=0 }
        in_sandbox && /^[[:space:]]*provisioner_url:[[:space:]]*/ {
            line=$0
            sub(/^[[:space:]]*provisioner_url:[[:space:]]*/, "", line)
            print line
            exit
        }
    ' "$DEER_FLOW_CONFIG_PATH")

    if [[ "$sandbox_use" == *"deerflow.community.aio_sandbox:AioSandboxProvider"* ]]; then
        if [ -n "$provisioner_url" ]; then
            echo "provisioner"
        else
            echo "aio"
        fi
    else
        echo "local"
    fi
}

select_services() {
    local sandbox_mode="$1"
    local services="frontend gateway nginx"

    if [ "$sandbox_mode" = "provisioner" ]; then
        services="$services provisioner"
    fi

    echo "$services"
}

require_sandbox_runtime() {
    local sandbox_mode="$1"

    if [ "$sandbox_mode" != "local" ] && [ ! -S "$DEER_FLOW_DOCKER_SOCKET" ]; then
        error "Docker socket not found at $DEER_FLOW_DOCKER_SOCKET."
        echo "AioSandboxProvider needs Docker-outside-of-Docker access."
        echo "Set DEER_FLOW_DOCKER_SOCKET to the host Docker socket path, or switch to local/provisioner sandbox mode."
        exit 1
    fi

    if [ "$sandbox_mode" != "local" ]; then
        ok "Docker socket: $DEER_FLOW_DOCKER_SOCKET"
    fi
}

print_config() {
    local sandbox_mode
    local services
    local secret_status="generated on start if missing"

    set_default_env
    sandbox_mode="$(detect_sandbox_mode)"
    services="$(select_services "$sandbox_mode")"

    if [ -n "${BETTER_AUTH_SECRET:-}" ]; then
        secret_status="set from environment"
    elif [ -f "$DEER_FLOW_HOME/.better-auth-secret" ]; then
        secret_status="stored at $DEER_FLOW_HOME/.better-auth-secret"
    fi

    echo "DeerFlow production Docker configuration"
    echo ""
    echo "Repo root:          $REPO_ROOT"
    echo "Compose file:       $DOCKER_DIR/docker-compose.yaml"
    echo "Compose project:    deer-flow"
    echo "Public URL:         http://localhost:${PORT:-2026}"
    echo "Sandbox mode:       $sandbox_mode"
    echo "Services:           $services"
    echo "DEER_FLOW_HOME:     $DEER_FLOW_HOME"
    echo "Config path:        $DEER_FLOW_CONFIG_PATH"
    echo "Extensions config:  $DEER_FLOW_EXTENSIONS_CONFIG_PATH"
    echo "Docker socket:      $DEER_FLOW_DOCKER_SOCKET"
    echo "BETTER_AUTH_SECRET: $secret_status"
}

show_banner() {
    echo "=========================================="
    echo "  DeerFlow Production Deployment"
    echo "=========================================="
    echo ""
}

show_running_message() {
    echo ""
    echo "=========================================="
    echo "  DeerFlow is running"
    echo "=========================================="
    echo ""
    echo "  Application: http://localhost:${PORT:-2026}"
    echo "  API Gateway: http://localhost:${PORT:-2026}/api/*"
    echo "  Runtime:     Gateway embedded"
    echo "  API:         /api/langgraph/* -> Gateway"
    echo ""
    echo "  Manage:"
    echo "    make down        - stop and remove containers"
    echo "    make prod-status - show container status"
    echo "    make prod-logs   - view logs"
    echo ""
}

run_build() {
    show_banner
    require_docker
    ensure_runtime_files

    echo ""
    echo "Building production images..."
    "${COMPOSE_CMD[@]}" build

    echo ""
    ok "Images built successfully"
    echo "Next: scripts/deploy.sh start"
}

run_start() {
    local sandbox_mode
    local services

    show_banner
    require_docker
    ensure_runtime_files
    sandbox_mode="$(detect_sandbox_mode)"
    services="$(select_services "$sandbox_mode")"

    info "Sandbox mode: $sandbox_mode"
    info "Runtime: Gateway embedded agent runtime"
    require_sandbox_runtime "$sandbox_mode"

    echo ""
    echo "Starting containers (no rebuild)..."
    # shellcheck disable=SC2086
    "${COMPOSE_CMD[@]}" up -d --remove-orphans $services
    show_running_message
}

run_up() {
    local sandbox_mode
    local services

    show_banner
    require_docker
    ensure_runtime_files
    sandbox_mode="$(detect_sandbox_mode)"
    services="$(select_services "$sandbox_mode")"

    info "Sandbox mode: $sandbox_mode"
    info "Runtime: Gateway embedded agent runtime"
    require_sandbox_runtime "$sandbox_mode"

    echo ""
    echo "Building images and starting containers..."
    # shellcheck disable=SC2086
    "${COMPOSE_CMD[@]}" up --build -d --remove-orphans $services
    show_running_message
}

run_down() {
    require_docker
    ensure_down_env
    "${COMPOSE_CMD[@]}" down
}

run_status() {
    require_docker
    ensure_down_env
    "${COMPOSE_CMD[@]}" ps
}

run_logs() {
    local service="${1:-}"

    case "$service" in
        ""|nginx|frontend|gateway|provisioner)
            ;;
        *)
            error "Unknown service for logs: $service"
            echo "Usage: scripts/deploy.sh logs [nginx|frontend|gateway|provisioner]" >&2
            exit 1
            ;;
    esac

    require_docker
    ensure_down_env
    if [ -n "$service" ]; then
        "${COMPOSE_CMD[@]}" logs -f "$service"
    else
        "${COMPOSE_CMD[@]}" logs -f
    fi
}

run_restart() {
    local sandbox_mode
    local services

    show_banner
    require_docker
    ensure_runtime_files
    sandbox_mode="$(detect_sandbox_mode)"
    services="$(select_services "$sandbox_mode")"
    require_sandbox_runtime "$sandbox_mode"

    echo ""
    echo "Restarting production containers..."
    # shellcheck disable=SC2086
    "${COMPOSE_CMD[@]}" restart $services
    show_running_message
}

main() {
    local command="${1:-up}"

    case "$command" in
        help|--help|-h)
            if [ -n "${2:-}" ]; then
                error "Unknown argument for help: $2"
                usage >&2
                exit 1
            fi
            usage
            ;;
        up)
            if [ -n "${2:-}" ]; then
                error "Unknown argument for up: $2"
                usage >&2
                exit 1
            fi
            run_up
            ;;
        build)
            if [ -n "${2:-}" ]; then
                error "Unknown argument for build: $2"
                usage >&2
                exit 1
            fi
            run_build
            ;;
        start)
            if [ -n "${2:-}" ]; then
                error "Unknown argument for start: $2"
                usage >&2
                exit 1
            fi
            run_start
            ;;
        restart)
            if [ -n "${2:-}" ]; then
                error "Unknown argument for restart: $2"
                usage >&2
                exit 1
            fi
            run_restart
            ;;
        down)
            if [ -n "${2:-}" ]; then
                error "Unknown argument for down: $2"
                usage >&2
                exit 1
            fi
            run_down
            ;;
        status)
            if [ -n "${2:-}" ]; then
                error "Unknown argument for status: $2"
                usage >&2
                exit 1
            fi
            run_status
            ;;
        logs)
            if [ -n "${3:-}" ]; then
                error "Unknown argument for logs: $3"
                usage >&2
                exit 1
            fi
            run_logs "${2:-}"
            ;;
        config)
            if [ -n "${2:-}" ]; then
                error "Unknown argument for config: $2"
                usage >&2
                exit 1
            fi
            print_config
            ;;
        *)
            echo "Unknown command: $command" >&2
            usage >&2
            exit 1
            ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
