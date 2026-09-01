#!/usr/bin/env bash
#
# monitor-sandbox-disk.sh - Watch DeerFlow AIO sandbox disk usage and log a breakdown.
#
# Cron (every 10 minutes):
#   */10 * * * * /opt/deer-flow/scripts/monitor-sandbox-disk.sh >> /var/log/deer-flow/sandbox-disk.log 2>&1
#
# Test Feishu webhook only:
#   /opt/deer-flow/scripts/monitor-sandbox-disk.sh --test-alert
#
# Env:
#   SANDBOX_CONTAINER_PREFIX     default deer-flow-sandbox
#   SANDBOX_DISK_WARN_GB         default 100
#   SANDBOX_DISK_LOG_DIR         default /var/log/deer-flow
#   DOCKER_ROOT                  default /var/lib/docker (or docker info data-root)
#   DEER_FLOW_HOME               default /var/lib/deer-flow
#   SANDBOX_IMAGE_PATTERN        default deer-flow-sandbox|all-in-one-sandbox
#   SANDBOX_DISK_FEISHU_WEBHOOK  Feishu bot v2 webhook URL (msg_type=text)
#   SANDBOX_DISK_ALERT_CMD       optional extra shell command after Feishu notify
#

set -euo pipefail

PREFIX="${SANDBOX_CONTAINER_PREFIX:-deer-flow-sandbox}"
WARN_GB="${SANDBOX_DISK_WARN_GB:-100}"
LOG_DIR="${SANDBOX_DISK_LOG_DIR:-/var/log/deer-flow}"
DEER_FLOW_HOME="${DEER_FLOW_HOME:-/var/lib/deer-flow}"
IMAGE_PATTERN="${SANDBOX_IMAGE_PATTERN:-deer-flow-sandbox|all-in-one-sandbox}"
# Feishu custom bot webhook (bot/v2). Override via env if rotated.
FEISHU_WEBHOOK="${SANDBOX_DISK_FEISHU_WEBHOOK:-https://open.feishu.cn/open-apis/bot/v2/hook/b33ec812-639f-4198-9ad9-6d684bc1463d}"

mkdir -p "$LOG_DIR"
REPORT="${LOG_DIR}/sandbox-disk-$(date +%Y%m%d).log"
ALERT_STATE="${LOG_DIR}/sandbox-disk-alert.state"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log_section() {
  echo ""
  echo "----- $* -----"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    log "ERROR: required command not found: $1"
    exit 1
  fi
}

bytes_to_gb() {
  awk -v b="${1:-0}" 'BEGIN { printf "%.2f", b / 1024 / 1024 / 1024 }'
}

dir_size_bytes() {
  local path="$1"
  if [ -d "$path" ]; then
    du -sb "$path" 2>/dev/null | awk '{print $1}'
  else
    echo 0
  fi
}

docker_root_dir() {
  if [ -n "${DOCKER_ROOT:-}" ]; then
    echo "$DOCKER_ROOT"
    return
  fi
  if command -v docker >/dev/null 2>&1; then
    docker info --format '{{.DockerRootDir}}' 2>/dev/null || true
  fi
}

resolve_container_log_paths() {
  docker ps -aq --filter "name=${PREFIX}" 2>/dev/null | while read -r cid; do
    [ -n "$cid" ] || continue
    docker inspect -f '{{.LogPath}}' "$cid" 2>/dev/null || true
  done
}

sum_sandbox_container_log_bytes() {
  local total=0
  local path
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    [ -f "$path" ] || continue
    local size
    size=$(stat -c '%s' "$path" 2>/dev/null || stat -f '%z' "$path" 2>/dev/null || echo 0)
    total=$((total + size))
  done < <(resolve_container_log_paths)
  echo "$total"
}

sum_sandbox_image_bytes() {
  if ! command -v docker >/dev/null 2>&1; then
    echo 0
    return
  fi
  local total=0
  local repo tag id size
  while read -r repo tag id; do
    [ -n "$id" ] || continue
    size=$(docker inspect -f '{{.Size}}' "$id" 2>/dev/null || echo 0)
    total=$((total + size))
  done < <(
    docker images --format '{{.Repository}} {{.Tag}} {{.ID}}' 2>/dev/null \
      | awk -v pat="$IMAGE_PATTERN" '$1 ~ pat { print $1, $2, $3 }'
  )
  echo "$total"
}

parse_docker_size_to_bytes() {
  # docker ps --size reports e.g. "1.2GB (virtual 3GB)" — take the writable part.
  echo "$1" | awk '{
    token = $1
    gsub(/[^0-9.]/, "", token)
    if ($1 ~ /GB$/) printf "%.0f\n", token * 1024 * 1024 * 1024
    else if ($1 ~ /MB$/) printf "%.0f\n", token * 1024 * 1024
    else if ($1 ~ /KB$/) printf "%.0f\n", token * 1024
    else if ($1 ~ /B$/) printf "%.0f\n", token
    else printf "0\n"
  }'
}

sum_sandbox_container_layer_bytes() {
  if ! command -v docker >/dev/null 2>&1; then
    echo 0
    return
  fi
  local total=0
  local name size_line size_bytes
  while read -r name size_line; do
    [ -n "$name" ] || continue
    case "$name" in
      "${PREFIX}-"*) ;;
      *) continue ;;
    esac
    size_bytes=$(parse_docker_size_to_bytes "$size_line")
    total=$((total + size_bytes))
  done < <(
    docker ps -a --filter "name=${PREFIX}" --format '{{.Names}} {{.Size}}' 2>/dev/null
  )
  echo "$total"
}

json_escape_string() {
  # Escape a string for JSON without python/jq (CentOS hosts often lack both).
  printf '%s' "$1" | awk '
    BEGIN { ORS="" }
    {
      if (NR > 1) printf "\\n"
      line = $0
      gsub(/\\/, "\\\\", line)
      gsub(/"/, "\\\"", line)
      gsub(/\t/, "\\t", line)
      gsub(/\r/, "", line)
      printf "%s", line
    }
  '
}

send_feishu_text() {
  local text="$1"
  local payload response http_code body escaped

  if [ -z "$FEISHU_WEBHOOK" ]; then
    log "WARN: Feishu webhook empty, skip notify"
    return 1
  fi

  # Feishu bot/v2 requires msg_type; bare {"text":...} returns code 19002.
  # https://open.feishu.cn/document/client-docs/bot-v2/add-custom-bot
  escaped=$(json_escape_string "$text")
  payload=$(printf '{"msg_type":"text","content":{"text":"%s"}}' "$escaped")

  response=$(curl -sS -w "\n%{http_code}" -X POST \
    -H "Content-Type: application/json; charset=utf-8" \
    -d "$payload" \
    "$FEISHU_WEBHOOK" 2>&1) || {
    log "WARN: Feishu curl failed: $response"
    return 1
  }

  http_code=$(echo "$response" | tail -n1)
  body=$(echo "$response" | sed '$d')
  if [ "$http_code" != "200" ]; then
    log "WARN: Feishu HTTP ${http_code}: ${body}"
    return 1
  fi
  if echo "$body" | grep -Eq '"code"[[:space:]]*:[[:space:]]*0|"StatusCode"[[:space:]]*:[[:space:]]*0|"msg"[[:space:]]*:[[:space:]]*"success"'; then
    log "Feishu alert sent"
    return 0
  fi
  log "WARN: Feishu unexpected response: ${body}"
  return 1
}

maybe_send_alert() {
  local total_gb="$1"
  local msg="$2"
  local now last force="${3:-0}"

  now=$(date +%s)
  last=0
  if [ -f "$ALERT_STATE" ]; then
    last=$(cat "$ALERT_STATE" 2>/dev/null || echo 0)
  fi

  # Suppress repeat alerts for 1 hour unless usage keeps rising (or force).
  if [ "$force" != "1" ] && [ -f "$ALERT_STATE.last_gb" ]; then
    local last_gb
    last_gb=$(cat "$ALERT_STATE.last_gb" 2>/dev/null || echo 0)
    if awk -v cur="$total_gb" -v prev="$last_gb" 'BEGIN { exit !(cur <= prev + 1) }'; then
      if [ $((now - last)) -lt 3600 ]; then
        return
      fi
    fi
  fi

  echo "$now" >"$ALERT_STATE"
  echo "$total_gb" >"$ALERT_STATE.last_gb"

  log "ALERT: sandbox-attributable disk ${total_gb}GB >= ${WARN_GB}GB threshold"
  log "ALERT: ${msg}"

  local host
  host=$(hostname 2>/dev/null || echo unknown)
  local text
  text=$(printf '【DeerFlow 沙箱磁盘告警】\n主机: %s\n沙箱相关占用: %s GB（阈值 %s GB）\n说明: %s\n详情日志: %s' \
    "$host" "$total_gb" "$WARN_GB" "$msg" "$REPORT")

  send_feishu_text "$text" || true

  if [ -n "${SANDBOX_DISK_ALERT_CMD:-}" ]; then
    # shellcheck disable=SC2090
    eval "$SANDBOX_DISK_ALERT_CMD" || log "WARN: SANDBOX_DISK_ALERT_CMD failed"
  fi
}

if [ "${1:-}" = "--test-alert" ]; then
  REPORT="${REPORT} (test)"
  maybe_send_alert "0.00" "这是一条连通性测试消息，可忽略" 1
  exit 0
fi

require_command docker
require_command du
require_command awk

DOCKER_ROOT_DIR="$(docker_root_dir)"
if [ -z "$DOCKER_ROOT_DIR" ]; then
  DOCKER_ROOT_DIR="/var/lib/docker"
fi

RUNNING_COUNT=$(docker ps -q --filter "name=${PREFIX}" 2>/dev/null | wc -l | tr -d ' ')
EXITED_COUNT=$(docker ps -aq --filter "name=${PREFIX}" --filter "status=exited" 2>/dev/null | wc -l | tr -d ' ')
DEAD_COUNT=$(docker ps -aq --filter "name=${PREFIX}" --filter "status=dead" 2>/dev/null | wc -l | tr -d ' ')

CONTAINER_LAYER_BYTES=$(sum_sandbox_container_layer_bytes)
CONTAINER_LOG_BYTES=$(sum_sandbox_container_log_bytes)
IMAGE_BYTES=$(sum_sandbox_image_bytes)
THREAD_DATA_BYTES=$(dir_size_bytes "${DEER_FLOW_HOME}/threads")

SANDBOX_TOTAL_BYTES=$((CONTAINER_LAYER_BYTES + CONTAINER_LOG_BYTES + IMAGE_BYTES))
SANDBOX_TOTAL_GB=$(bytes_to_gb "$SANDBOX_TOTAL_BYTES")
THREAD_DATA_GB=$(bytes_to_gb "$THREAD_DATA_BYTES")
DOCKER_ROOT_BYTES=$(dir_size_bytes "$DOCKER_ROOT_DIR")
DOCKER_ROOT_GB=$(bytes_to_gb "$DOCKER_ROOT_BYTES")

{
  log "=== DeerFlow sandbox disk report ==="
  log "prefix=${PREFIX} warn_gb=${WARN_GB} docker_root=${DOCKER_ROOT_DIR} deer_flow_home=${DEER_FLOW_HOME}"
  log "summary sandbox_attributable_gb=${SANDBOX_TOTAL_GB} container_layers_gb=$(bytes_to_gb "$CONTAINER_LAYER_BYTES") container_logs_gb=$(bytes_to_gb "$CONTAINER_LOG_BYTES") images_gb=$(bytes_to_gb "$IMAGE_BYTES") thread_data_gb=${THREAD_DATA_GB} docker_root_gb=${DOCKER_ROOT_GB}"
  log "summary containers running=${RUNNING_COUNT} exited=${EXITED_COUNT} dead=${DEAD_COUNT}"

  log_section "host filesystem"
  df -h "$DOCKER_ROOT_DIR" 2>/dev/null || df -h /

  log_section "docker system df"
  docker system df -v 2>/dev/null | head -80 || docker system df 2>/dev/null || true

  log_section "sandbox containers (all states)"
  docker ps -a --filter "name=${PREFIX}" \
    --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.RunningFor}}\t{{.Size}}' 2>/dev/null || true

  log_section "top sandbox container logs"
  resolve_container_log_paths | while read -r path; do
    [ -f "$path" ] || continue
    du -h "$path" 2>/dev/null
  done | sort -hr | head -10 || true

  log_section "sandbox-related images"
  docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}\t{{.CreatedSince}}' 2>/dev/null \
    | awk -v pat="$IMAGE_PATTERN" 'NR==1 || $0 ~ pat' || true

  log_section "largest thread workspaces (bind-mount, not overlay)"
  if [ -d "${DEER_FLOW_HOME}/threads" ]; then
    du -sh "${DEER_FLOW_HOME}/threads"/* 2>/dev/null | sort -hr | head -10 || true
  else
    echo "(no ${DEER_FLOW_HOME}/threads directory)"
  fi

  log_section "analysis hints"
  if [ "$EXITED_COUNT" -gt 0 ] || [ "$DEAD_COUNT" -gt 0 ]; then
    echo "- Found exited/dead sandbox containers: likely failed --rm auto-removal; run scripts/cleanup-containers.sh or deploy prune_dead_containers patch"
  fi
  if awk -v logs="$(bytes_to_gb "$CONTAINER_LOG_BYTES")" 'BEGIN { exit !(logs > 5) }'; then
    echo "- Container logs are large: add daemon.json log-opts max-size/max-file"
  fi
  if awk -v imgs="$(bytes_to_gb "$IMAGE_BYTES")" 'BEGIN { exit !(imgs > 10) }'; then
    echo "- Sandbox images are large: check dangling images after rebuild (docker images -f dangling=true)"
  fi
  if awk -v threads="$THREAD_DATA_GB" 'BEGIN { exit !(threads > 20) }'; then
    echo "- Thread bind-mount data is large: inspect top workspaces above; consider moving DEER_FLOW_HOME"
  fi
  if awk -v layers="$(bytes_to_gb "$CONTAINER_LAYER_BYTES")" 'BEGIN { exit !(layers > 30) }'; then
    echo "- Container writable layers are large: enhanced image tasks may write to /tmp or caches inside overlay; check running container count and idle_timeout"
  fi

  log "=== end report ==="
} | tee -a "$REPORT"

THRESHOLD_BYTES=$((WARN_GB * 1024 * 1024 * 1024))
if [ "$SANDBOX_TOTAL_BYTES" -ge "$THRESHOLD_BYTES" ]; then
  maybe_send_alert "$SANDBOX_TOTAL_GB" "See ${REPORT} for breakdown"
  exit 2
fi

exit 0
