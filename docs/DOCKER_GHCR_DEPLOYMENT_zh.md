# DeerFlow GHCR 预构建镜像部署手册（CentOS 7）

本文面向以下场景：

- 在 **CentOS 7（x86_64）** 等老系统上部署，**不在服务器本地编译**前后端镜像。
- 使用 **fork / 定制代码**，通过 GitHub Actions 构建 **amd64** 镜像并推送到 GHCR。
- 开发机在 **Apple Silicon（arm64）Mac** 上，无法直接把本地 `docker build` 产物用于生产服务器。

与「服务器本地 `make up` 构建」的流程不同，请参阅 [DOCKER_PRODUCTION_DEPLOYMENT_zh.md](./DOCKER_PRODUCTION_DEPLOYMENT_zh.md)。

## 架构概览

```
Mac（arm64 开发）
  → push main + tag v*
  → GitHub Actions（ubuntu-latest，amd64）
  → ghcr.io/<owner>/deer-flow-backend:<tag>
  → ghcr.io/<owner>/deer-flow-frontend:<tag>

CentOS 7 服务器
  → git clone（拿 compose / nginx / scripts / 配置模板）
  → docker compose pull
  → 只启动 frontend + gateway + nginx
```

生产拓扑（与标准 Docker 部署相同）：

| 服务 | 说明 |
| --- | --- |
| `nginx` | 统一入口，默认端口 `2026` |
| `frontend` | Next.js 生产服务 |
| `gateway` | FastAPI Gateway + 内嵌 agent runtime |

**不启动** `provisioner`（除非 `config.yaml` 明确配置 K8s provisioner 模式；GHCR 流程默认不需要）。

## 环境与限制

### 服务器要求

- CentOS 7.x，**x86_64**
- Docker CE 20.10+（建议 24.x / 25.x）
- Docker Compose v2
- 内存建议 **≥ 8 GB**
- 可访问 `ghcr.io`（国内慢时可配 registry mirror 或私有仓库）

### 不要在 CentOS 7 宿主机上执行

| 命令 | 原因 |
| --- | --- |
| `make setup` / `make install` | 宿主机 glibc 2.17，`onnxruntime` 等 wheel 不兼容 |
| `make up` | 会在服务器本地 build，慢且易失败 |
| 裸 `docker compose up -d` | 会尝试 build 可选的 `provisioner` 服务 |

应用在 **容器内**（Debian bookworm）运行，与 CentOS 7 宿主机 glibc 无关；宿主机只需 Docker + Git。

### Mac 与服务器架构

| 构建来源 | 镜像架构 | CentOS 7 能否运行 |
| --- | --- | --- |
| Mac 默认 `docker build` | `linux/arm64` | **不能**（`exec format error`） |
| Mac + `--platform linux/amd64` | `linux/amd64` | 可以，但跨平台构建很慢 |
| GitHub Actions / GHCR | `linux/amd64` | **推荐** |

---

## 一、发布镜像（Mac / GitHub）

### 1. 推送代码

确保定制代码已推到 fork 的 `main`（或你的发布分支）：

```bash
git push origin main
```

### 2. 打 tag 触发 CI

仓库内 [`.github/workflows/container.yaml`](../.github/workflows/container.yaml) 在推送 `v*` tag 时构建并推送镜像：

```bash
git tag v1.0.0
git push origin v1.0.0
```

在 GitHub **Actions → Publish Containers** 确认任务成功。

### 3. 镜像地址

将 `<owner>` 换成你的 GitHub 用户名或组织名（例如 `xlows-1227`）：

| 服务 | 镜像 |
| --- | --- |
| gateway | `ghcr.io/<owner>/deer-flow-backend:<tag>` |
| frontend | `ghcr.io/<owner>/deer-flow-frontend:<tag>` |

### 4. 首次发布：GHCR 包可见性

Fork 新建的 GHCR 包默认可能为 **Private**。若服务器 `docker pull` 报 `UNAUTHORIZED`：

1. GitHub → **Packages** → `deer-flow-backend` / `deer-flow-frontend`
2. **Package settings → Change visibility → Public**

或使用 PAT 登录：

```bash
echo <GITHUB_PAT> | docker login ghcr.io -u <owner> --password-stdin
```

---

## 二、服务器首次部署

### 1. 克隆仓库

```bash
git clone -b main https://github.com/<owner>/deer-flow.git
cd deer-flow
```

### 2. 准备配置

**不要**在服务器上运行 `make setup`。任选其一：

```bash
# 方式 A：复制模板
cp config.example.yaml config.yaml
cp .env.example .env
cp extensions_config.example.json extensions_config.json
cp frontend/.env.example frontend/.env

# 方式 B：从开发机 scp 已配好的文件（推荐）
# scp config.yaml .env extensions_config.json frontend/.env root@<server>:/path/to/deer-flow/
```

编辑 `config.yaml`、`.env`，填入模型 API Key 等。若 `make config` 报 `python3: Command not found`，用方式 A 或：

```bash
PYTHON=/usr/local/bin/python3.8 make config
```

### 3. 创建 GHCR Compose 覆盖文件

在服务器创建 `docker/docker-compose.images.yaml`（此文件不随上游仓库提供，需按部署环境填写 `<owner>`）：

```yaml
services:
  frontend:
    image: ghcr.io/<owner>/deer-flow-frontend:${DEER_FLOW_IMAGE_TAG}
    build: !reset null

  gateway:
    image: ghcr.io/<owner>/deer-flow-backend:${DEER_FLOW_IMAGE_TAG}
    build: !reset null
    security_opt:
      - seccomp:unconfined
    pids_limit: -1
    ulimits:
      nproc: 65535
      nofile:
        soft: 65535
        hard: 65535
    command: >-
      sh -c "cd backend && PYTHONPATH=. uv run --no-sync uvicorn app.gateway.app:app
      --host 0.0.0.0 --port 8001"
```

**配置说明：**

| 项 | 作用 |
| --- | --- |
| `build: !reset null` | 禁用本地 build，仅使用 GHCR 镜像 |
| `uv run --no-sync` | 启动时不访问 PyPI（避免国内超时） |
| 不使用 `--workers` | 单进程 uvicorn，减少线程/进程占用 |
| `seccomp:unconfined` | CentOS 7 上 Python 3.12 创建线程所需（`can't start new thread`） |
| `ulimits.nproc` | 提高容器内线程上限 |

> `build: !reset null` 需要 Docker Compose **v2.24+**。若报错，请升级 compose 插件。

### 4. 设置环境变量

写入 `.env` 或在 shell 中 `export`：

```bash
export DEER_FLOW_IMAGE_TAG=v1.0.0
export DEER_FLOW_REPO_ROOT="$(pwd)"
export DEER_FLOW_HOME="${DEER_FLOW_HOME:-$DEER_FLOW_REPO_ROOT/backend/.deer-flow}"
export DEER_FLOW_CONFIG_PATH="${DEER_FLOW_CONFIG_PATH:-$DEER_FLOW_REPO_ROOT/config.yaml}"
export DEER_FLOW_EXTENSIONS_CONFIG_PATH="${DEER_FLOW_EXTENSIONS_CONFIG_PATH:-$DEER_FLOW_REPO_ROOT/extensions_config.json}"
export DEER_FLOW_DOCKER_SOCKET="${DEER_FLOW_DOCKER_SOCKET:-/var/run/docker.sock}"
export BETTER_AUTH_SECRET="${BETTER_AUTH_SECRET:-$(openssl rand -hex 32)}"

mkdir -p "$DEER_FLOW_HOME"
mkdir -p "$DEER_FLOW_REPO_ROOT/skills/custom"
```

缺少 `DEER_FLOW_CONFIG_PATH` 等变量时，compose 会解析出无效挂载 `:/app/backend/config.yaml:ro` 并报错。

### 5. 拉取镜像并启动

```bash
docker compose -p deer-flow \
  -f docker/docker-compose.yaml \
  -f docker/docker-compose.images.yaml \
  pull

docker compose -p deer-flow \
  -f docker/docker-compose.yaml \
  -f docker/docker-compose.images.yaml \
  up -d --no-build frontend gateway nginx
```

**务必**：

- 只启动 `frontend gateway nginx` 三个服务
- 加 `--no-build`，避免在服务器上编译 `provisioner` 等带 `build:` 的服务

### 6. 验证

```bash
docker compose -p deer-flow ps
docker compose -p deer-flow logs gateway --tail 30
curl -s http://127.0.0.1:2026/api/health
```

浏览器访问：`http://<服务器IP>:2026`

---

## 三、日常更新

### 有代码变更

**开发机：**

```bash
git push origin main
git tag v1.0.1 && git push origin v1.0.1
# 等待 Actions 完成
```

**服务器：**

```bash
cd deer-flow
git pull origin main
export DEER_FLOW_IMAGE_TAG=v1.0.1

docker compose -p deer-flow \
  -f docker/docker-compose.yaml \
  -f docker/docker-compose.images.yaml \
  pull

docker compose -p deer-flow \
  -f docker/docker-compose.yaml \
  -f docker/docker-compose.images.yaml \
  up -d --no-build frontend gateway nginx
```

### 仅改配置（config.yaml / .env）

无需 pull 或打 tag：

```bash
docker compose -p deer-flow restart gateway frontend
```

---

## 四、常用运维命令

```bash
# 状态
docker compose -p deer-flow ps

# 日志
docker compose -p deer-flow logs gateway --tail 50
docker compose -p deer-flow logs frontend --tail 50
docker compose -p deer-flow logs nginx --tail 50

# 重启单个服务
docker compose -p deer-flow restart gateway

# 停止（不删除数据目录）
docker compose -p deer-flow \
  -f docker/docker-compose.yaml \
  -f docker/docker-compose.images.yaml \
  down
```

运行期数据默认在 `DEER_FLOW_HOME`（默认 `backend/.deer-flow`），`down` 不会删除该目录。

---

## 五、一键部署脚本（可选）

在仓库根目录保存 `scripts/deploy-ghcr.sh`：

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

export DEER_FLOW_IMAGE_TAG="${DEER_FLOW_IMAGE_TAG:?请设置 DEER_FLOW_IMAGE_TAG，例如 v1.0.0}"
export DEER_FLOW_REPO_ROOT="$REPO_ROOT"
export DEER_FLOW_HOME="${DEER_FLOW_HOME:-$REPO_ROOT/backend/.deer-flow}"
export DEER_FLOW_CONFIG_PATH="${DEER_FLOW_CONFIG_PATH:-$REPO_ROOT/config.yaml}"
export DEER_FLOW_EXTENSIONS_CONFIG_PATH="${DEER_FLOW_EXTENSIONS_CONFIG_PATH:-$REPO_ROOT/extensions_config.json}"
export DEER_FLOW_DOCKER_SOCKET="${DEER_FLOW_DOCKER_SOCKET:-/var/run/docker.sock}"
export BETTER_AUTH_SECRET="${BETTER_AUTH_SECRET:-$(openssl rand -hex 32)}"

mkdir -p "$DEER_FLOW_HOME"
if [ ! -f "$DEER_FLOW_CONFIG_PATH" ]; then
  cp config.example.yaml "$DEER_FLOW_CONFIG_PATH"
  echo "已从 config.example.yaml 生成 config.yaml，请编辑后重新运行。"
  exit 1
fi

if [ ! -f docker/docker-compose.images.yaml ]; then
  echo "缺少 docker/docker-compose.images.yaml，请先按部署手册创建。"
  exit 1
fi

COMPOSE=(docker compose -p deer-flow
  -f docker/docker-compose.yaml
  -f docker/docker-compose.images.yaml)

"${COMPOSE[@]}" pull
"${COMPOSE[@]}" up -d --no-build frontend gateway nginx

echo "DeerFlow: http://localhost:${PORT:-2026}"
```

使用：

```bash
chmod +x scripts/deploy-ghcr.sh
export DEER_FLOW_IMAGE_TAG=v1.0.0
./scripts/deploy-ghcr.sh
```

---

## 六、排障速查

| 现象 | 可能原因 | 处理 |
| --- | --- | --- |
| `exec format error` | 使用了 arm64 镜像 | 改用 GHCR CI 构建的 amd64 镜像 |
| `make setup` / `onnxruntime` 失败 | CentOS 7 glibc 过旧 | 不在宿主机装 Python 依赖；复制或 scp 配置 |
| `python3: Command not found` | 无 `python3` 命令 | `cp` 模板或 `PYTHON=/usr/local/bin/python3.8 make config` |
| `invalid spec: :/app/backend/config.yaml:ro` | 未 export `DEER_FLOW_*` | 见「二、4. 设置环境变量」 |
| 构建 `provisioner` / `runc -keep` | 裸 `up -d` 触发 build | 只启动 `frontend gateway nginx`，加 `--no-build` |
| 启动时访问 `pypi.org` 超时 | compose 覆盖未加 `--no-sync` | 按本文 gateway `command` 配置 |
| `can't start new thread` | seccomp 限制 Python 3.12 线程 | gateway 加 `security_opt: seccomp:unconfined` |
| 前端注册失败 | gateway 未就绪 | 先查 `docker compose logs gateway` |
| `UNAUTHORIZED` pull | GHCR 包为 Private | 改 Public 或 `docker login ghcr.io` |
| `ghcr.io` 很慢 | 网络限制 | registry mirror，或同步到阿里云 ACR 再 pull |
| Skill 发布失败 `Read-only file system: '/app/skills/custom'` | `skills` 目录被 `:ro` 只读挂载 | 确保 `docker-compose.yaml` 中 `../skills:/app/skills` **无** `:ro`；`mkdir -p skills/custom` 后重启 gateway |

### 诊断命令

```bash
# 内存
free -h

# 容器 ulimit
docker inspect deer-flow-gateway --format '{{json .HostConfig.Ulimits}}'

# 容器内线程测试（将 <tag> 换成实际版本）
docker run --rm --security-opt seccomp=unconfined \
  ghcr.io/<owner>/deer-flow-backend:<tag> \
  python3 -c "import threading; threading.Thread(target=lambda: None).start(); print('thread-ok')"

# Docker 组件版本（runc 应与 Engine 匹配，避免 rc8+dev 等异常版本）
docker version
```

若 `docker version` 显示 Engine 25.x 但 runc 为 `1.0.0-rc8+dev`，可尝试：

```bash
yum reinstall runc containerd.io docker-ce docker-ce-cli -y
systemctl restart docker
```

---

## 七、与本地构建部署的对比

| | GHCR pull（本文） | `make up` 本地构建 |
| --- | --- | --- |
| 服务器编译 | 不需要 | 需要（慢，CentOS 7 易踩坑） |
| 定制代码 | fork + 打 tag 触发 CI | 直接 build |
| Mac 交叉编译 | 不需要 | arm64 Mac 需 `--platform linux/amd64` |
| 首次部署耗时 | 约 5–15 分钟（pull） | 约 30–60+ 分钟 |
| 适用系统 | CentOS 7 等老 glibc 宿主机 | 建议较新 Linux（Debian/Ubuntu/Rocky 8+） |

---

## 相关文档

- [DOCKER_PRODUCTION_DEPLOYMENT_zh.md](./DOCKER_PRODUCTION_DEPLOYMENT_zh.md) — 服务器本地构建的标准生产部署
- [../docker/docker-compose.yaml](../docker/docker-compose.yaml) — 基础 Compose 定义
- [../scripts/deploy.sh](../scripts/deploy.sh) — 标准生产部署脚本（本地 build 流程）
