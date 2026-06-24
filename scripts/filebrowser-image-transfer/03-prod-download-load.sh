#!/usr/bin/env bash
#
# Run on the production server: copy DeerFlow image archives over SCP, verify
# them, optionally load them into Docker, and optionally deploy DeerFlow.

# Do not enable nounset here: macOS Bash 3.2 treats an empty array expansion as
# an unbound variable, while these scripts intentionally use optional arrays.
set -Eeo pipefail
umask 077

log() {
  printf '\n==> %s\n' "$*"
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "缺少命令: $1"
}

require_env() {
  local name="$1"
  [ -n "${!name:-}" ] || die "请先设置环境变量: $name"
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    die "缺少 sha256sum 或 shasum"
  fi
}

confirm_action() {
  local value="$1"
  local prompt="$2"
  local answer

  case "$value" in
    1|true|TRUE|yes|YES|y|Y)
      return 0
      ;;
    0|false|FALSE|no|NO|n|N)
      return 1
      ;;
    ask|"")
      ;;
    *)
      die "确认选项仅支持 ask、1 或 0，当前值: $value"
      ;;
  esac

  if [ ! -t 0 ]; then
    printf '当前为非交互环境，默认选择否：%s\n' "$prompt"
    return 1
  fi

  while true; do
    printf '%s [y/N] ' "$prompt"
    if ! IFS= read -r answer; then
      answer=""
    fi
    case "$answer" in
      y|Y|yes|YES)
        return 0
        ;;
      ""|n|N|no|NO)
        return 1
        ;;
      *)
        printf '请输入 y 或 n。\n'
        ;;
    esac
  done
}

select_docker_command() {
  if docker info >/dev/null 2>&1; then
    DOCKER=(docker)
  elif command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
    DOCKER=(sudo docker)
  else
    die "Docker daemon 不可用，请检查 Docker 或 sudo 权限"
  fi
}

scp_download() {
  local filename="$1"
  local output="$DOWNLOAD_DIR/$filename"
  local temp_output="${output}.part"
  local remote="${SCP_USER}@${SCP_HOST}:${SCP_REMOTE_DIR}/${filename}"

  log "SCP 拉取 $remote -> $output"
  rm -f "$temp_output"
  scp "${SCP_ARGS[@]}" "$remote" "$temp_output"
  mv "$temp_output" "$output"
}

verify_file() {
  local filename="$1"
  local expected actual

  expected="$(awk -v file="$filename" '$2 == file {print $1; exit}' \
    "$DOWNLOAD_DIR/$MANIFEST_FILE")"
  [ -n "$expected" ] || die "校验清单中缺少文件: $filename"
  actual="$(sha256_file "$DOWNLOAD_DIR/$filename")"
  [ "$actual" = "$expected" ] || die "SHA-256 校验失败: $filename"
  gzip -t "$DOWNLOAD_DIR/$filename" || die "gzip 文件损坏: $filename"
  printf '校验通过: %s\n' "$filename"
}

load_image() {
  local archive="$DOWNLOAD_DIR/$1"

  log "解压并导入 Docker 镜像: $archive"
  gunzip -c "$archive" | "${DOCKER[@]}" load
}

deploy_deer_flow() {
  [ -d "$DEPLOY_DIR" ] || die "部署目录不存在: $DEPLOY_DIR"
  [ -f "$DEPLOY_DIR/.env" ] || die "部署目录缺少 .env: $DEPLOY_DIR/.env"
  [ -f "$DEPLOY_DIR/docker/docker-compose.yaml" ] \
    || die "缺少 Compose 文件: $DEPLOY_DIR/docker/docker-compose.yaml"
  [ -f "$DEPLOY_DIR/docker/docker-compose.images.yaml" ] \
    || die "缺少 Compose 文件: $DEPLOY_DIR/docker/docker-compose.images.yaml"

  log "进入部署目录: $DEPLOY_DIR"
  cd "$DEPLOY_DIR"

  log "拉取最新代码"
  git pull

  export DEER_FLOW_IMAGE_TAG
  log "部署 DeerFlow，镜像标签: $DEER_FLOW_IMAGE_TAG"

  if [ "${DOCKER[0]}" = "sudo" ]; then
    sudo env "DEER_FLOW_IMAGE_TAG=$DEER_FLOW_IMAGE_TAG" \
      docker compose --env-file .env -p deer-flow \
      -f docker/docker-compose.yaml \
      -f docker/docker-compose.images.yaml \
      up -d --no-build frontend gateway nginx
  else
    docker compose --env-file .env -p deer-flow \
      -f docker/docker-compose.yaml \
      -f docker/docker-compose.images.yaml \
      up -d --no-build frontend gateway nginx
  fi

  log "DeerFlow 部署完成"
}

require_command scp
require_command gzip
require_env DEER_FLOW_IMAGE_TAG

[[ "$DEER_FLOW_IMAGE_TAG" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
  || die "DEER_FLOW_IMAGE_TAG 只能包含字母、数字、点、下划线和连字符"

SCP_HOST="${SCP_HOST:-172.20.62.36}"
SCP_USER="${SCP_USER:-root}"
SCP_PORT="${SCP_PORT:-22}"
SCP_REMOTE_DIR="${SCP_REMOTE_DIR:-/opt/tools/file_browser/deer-flow}"
DOWNLOAD_DIR="${TRANSFER_WORK_DIR:-$(pwd)}"
DEPLOY_DIR="${DEER_FLOW_DEPLOY_DIR:-/opt/deer-flow}"
LOAD_IMAGES_ACTION="${LOAD_IMAGES_ACTION:-ask}"
DEPLOY_AFTER_LOAD="${DEPLOY_AFTER_LOAD:-ask}"

FRONTEND_FILE="deer-flow-frontend-${DEER_FLOW_IMAGE_TAG}.tar.gz"
BACKEND_FILE="deer-flow-backend-${DEER_FLOW_IMAGE_TAG}.tar.gz"
MANIFEST_FILE="deer-flow-images-${DEER_FLOW_IMAGE_TAG}.sha256"

[[ "$SCP_HOST" =~ ^[A-Za-z0-9._:-]+$ ]] || die "SCP_HOST 包含不支持的字符"
[[ "$SCP_USER" =~ ^[A-Za-z0-9._-]+$ ]] || die "SCP_USER 包含不支持的字符"
[[ "$SCP_PORT" =~ ^[0-9]+$ ]] || die "SCP_PORT 必须是数字"
[[ "$SCP_REMOTE_DIR" = /* ]] || die "SCP_REMOTE_DIR 必须是绝对路径"
[[ "$SCP_REMOTE_DIR" =~ ^[A-Za-z0-9._/-]+$ ]] || die "SCP_REMOTE_DIR 包含不支持的字符"
[[ "$SCP_REMOTE_DIR" != *"/../"* ]] && [[ "$SCP_REMOTE_DIR" != *"/./"* ]] \
  || die "SCP_REMOTE_DIR 不能包含 . 或 .. 路径段"

SCP_ARGS=(
  -P "$SCP_PORT"
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=6
)
if [ "${SCP_ENABLE_LEGACY_SSH_RSA:-0}" = "1" ]; then
  SCP_ARGS+=(-o HostKeyAlgorithms=+ssh-rsa)
fi
if [ "${SCP_USE_LEGACY_PROTOCOL:-0}" = "1" ]; then
  SCP_ARGS+=(-O)
fi
if [ -n "${SCP_IDENTITY_FILE:-}" ]; then
  SCP_ARGS+=(-i "$SCP_IDENTITY_FILE")
fi

mkdir -p "$DOWNLOAD_DIR"

log "从生产文件服务器拉取镜像文件"
printf '来源: %s@%s:%s%s\n' "$SCP_USER" "$SCP_HOST" "$SCP_PORT" "$SCP_REMOTE_DIR"
printf '本地目录: %s\n' "$DOWNLOAD_DIR"

scp_download "$MANIFEST_FILE"
scp_download "$FRONTEND_FILE"
scp_download "$BACKEND_FILE"

log "校验 SCP 下载文件"
verify_file "$FRONTEND_FILE"
verify_file "$BACKEND_FILE"

if ! confirm_action "$LOAD_IMAGES_ACTION" "是否解压并导入 Docker 镜像？"; then
  log "已跳过镜像解压与导入"
  printf '文件保留在: %s\n' "$DOWNLOAD_DIR"
  exit 0
fi

require_command gunzip
require_command docker
select_docker_command

load_image "$FRONTEND_FILE"
load_image "$BACKEND_FILE"

IMAGE_REGISTRY="${IMAGE_REGISTRY:-ghcr.io/xlows-1227}"
IMAGE_REGISTRY="${IMAGE_REGISTRY%/}"
FRONTEND_IMAGE="${DEER_FLOW_FRONTEND_IMAGE:-${IMAGE_REGISTRY}/deer-flow-frontend:${DEER_FLOW_IMAGE_TAG}}"
BACKEND_IMAGE="${DEER_FLOW_BACKEND_IMAGE:-${IMAGE_REGISTRY}/deer-flow-backend:${DEER_FLOW_IMAGE_TAG}}"

"${DOCKER[@]}" image inspect "$FRONTEND_IMAGE" >/dev/null 2>&1 \
  || die "导入后未找到预期前端镜像: $FRONTEND_IMAGE"
"${DOCKER[@]}" image inspect "$BACKEND_IMAGE" >/dev/null 2>&1 \
  || die "导入后未找到预期后端镜像: $BACKEND_IMAGE"

log "Docker 镜像导入完成"
printf '前端镜像: %s\n' "$FRONTEND_IMAGE"
printf '后端镜像: %s\n' "$BACKEND_IMAGE"

if ! confirm_action "$DEPLOY_AFTER_LOAD" "镜像已导入，是否立即部署 DeerFlow？"; then
  log "已跳过部署"
  exit 0
fi

require_command git
deploy_deer_flow
