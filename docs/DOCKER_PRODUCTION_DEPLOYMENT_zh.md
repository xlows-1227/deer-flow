# DeerFlow Docker 生产部署手册

本文面向需要在服务器上长期运行 DeerFlow 的部署者。生产 Docker 部署使用 `docker/docker-compose.yaml`，入口脚本是 `scripts/deploy.sh`，常用命令也已封装到 `Makefile`。

## 适用场景

- 需要在 Linux 服务器上长期运行 DeerFlow。
- 需要本地构建前后端镜像，并挂载运行期配置与数据。
- 需要通过统一的 nginx 入口访问 Web UI 和 Gateway API。

生产拓扑包含：

- `nginx`: 对外入口，默认监听 `2026`。
- `frontend`: Next.js 生产服务。
- `gateway`: FastAPI Gateway 和内嵌 agent runtime。
- `provisioner`: 仅在 `config.yaml` 配置 provisioner sandbox 模式时启动。

## 服务器要求

建议从下面配置起步：

| 场景 | 起步配置 | 推荐配置 |
| --- | --- | --- |
| 单人或轻量使用 | 8 vCPU, 16 GB 内存, 40 GB SSD | 16 vCPU, 32 GB 内存 |
| 多人共享或重任务 | 16 vCPU, 32 GB 内存, 80 GB SSD | 根据并发和 sandbox 任务继续扩容 |

如果同一台机器还运行本地大模型，请单独为模型服务预留 CPU、内存和显存。

## 前置条件

服务器需要具备：

- Git
- Docker Engine
- Docker Compose v2 插件
- 可访问模型服务或模型 API 的网络

验证 Docker：

```bash
docker info
docker compose version
```

如果 Linux 上遇到 Docker socket 权限错误，请把部署用户加入 `docker` 组并重新登录，或使用符合你们安全策略的 Docker 访问方式。

## 首次部署

从仓库根目录执行：

```bash
make setup
```

根据向导生成或检查 `config.yaml`。至少需要配置一个可用模型。

检查生产部署配置：

```bash
make prod-config
```

构建并启动生产服务：

```bash
make up
```

默认访问地址：

```text
http://localhost:2026
```

如果部署在远程服务器，请通过反向代理、VPN、SSH tunnel 或可信内网访问，不建议直接裸露到公网。

## 配置文件

生产部署默认使用以下文件：

| 配置 | 默认路径 | 说明 |
| --- | --- | --- |
| 主配置 | `config.yaml` | 模型、搜索、sandbox 等后端配置 |
| 扩展配置 | `extensions_config.json` | MCP 和 skills 扩展配置 |
| 前端环境 | `frontend/.env` | 前端生产环境变量 |
| 后端环境 | `.env` | Gateway 和 provisioner 的环境变量 |

可以通过环境变量覆盖配置路径：

```bash
export DEER_FLOW_CONFIG_PATH=/opt/deer-flow/config.yaml
export DEER_FLOW_EXTENSIONS_CONFIG_PATH=/opt/deer-flow/extensions_config.json
```

启动脚本不会覆盖已有配置文件。缺少配置文件时，会尽量从示例文件生成初始文件，并提示你补齐真实模型配置。

## 运行期数据

生产运行期数据默认写入：

```bash
backend/.deer-flow
```

建议在服务器部署时显式放到持久化目录：

```bash
export DEER_FLOW_HOME=/var/lib/deer-flow
make up
```

`DEER_FLOW_HOME` 中会保存运行期数据和自动生成的 `.better-auth-secret`。请把该目录纳入备份策略。`make down` 只停止并移除容器，不会删除该目录。

## 常用命令

查看解析后的生产配置：

```bash
make prod-config
```

构建并启动：

```bash
make up
```

只构建镜像：

```bash
scripts/deploy.sh build
```

使用已构建镜像启动：

```bash
scripts/deploy.sh start
```

查看容器状态：

```bash
make prod-status
```

查看全部日志：

```bash
make prod-logs
```

查看指定服务日志：

```bash
scripts/deploy.sh logs [service]
```

```bash
scripts/deploy.sh logs gateway
scripts/deploy.sh logs frontend
scripts/deploy.sh logs nginx
scripts/deploy.sh logs provisioner
```

重启生产服务：

```bash
make prod-restart
```

停止并移除生产容器：

```bash
make down
```

## 更新部署

拉取代码后重新构建并启动：

```bash
git pull
make up
```

如果你希望分两步执行：

```bash
scripts/deploy.sh build
scripts/deploy.sh start
```

更新前建议确认：

```bash
make prod-config
make prod-status
```

更新后检查：

```bash
make prod-status
scripts/deploy.sh logs gateway
```

## 常用环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `2026` | nginx 对外端口 |
| `DEER_FLOW_HOME` | `backend/.deer-flow` | 运行期数据目录 |
| `DEER_FLOW_CONFIG_PATH` | `config.yaml` | 主配置路径 |
| `DEER_FLOW_EXTENSIONS_CONFIG_PATH` | `extensions_config.json` | 扩展配置路径 |
| `DEER_FLOW_DOCKER_SOCKET` | `/var/run/docker.sock` | AIO Docker sandbox 需要的宿主机 Docker socket |
| `BETTER_AUTH_SECRET` | 自动生成并保存 | 前端认证和会话密钥 |
| `UV_INDEX_URL` | `https://pypi.org/simple` | Python 依赖镜像 |
| `NPM_REGISTRY` | npm 默认 registry | 前端依赖镜像 |

国内或受限网络环境可先设置镜像：

```bash
export UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
export NPM_REGISTRY=https://registry.npmmirror.com
make up
```

不要把真实密钥提交到 Git。`.env`、`frontend/.env`、`config.yaml` 中的真实凭据应由部署环境管理。

## Sandbox 模式

`scripts/deploy.sh` 会根据 `config.yaml` 自动判断 sandbox 模式。

### local

未配置 AIO provider，或使用本地 provider 时，脚本只启动：

```text
frontend gateway nginx
```

### aio

当 `sandbox.use` 使用：

```yaml
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
```

且没有配置 `provisioner_url` 时，Gateway 会通过 Docker-outside-of-Docker 启动 sandbox 容器。此模式要求宿主机 Docker socket 可挂载到 Gateway 容器。

默认 socket：

```bash
/var/run/docker.sock
```

如果你的 Docker socket 在其他位置：

```bash
export DEER_FLOW_DOCKER_SOCKET=/path/to/docker.sock
make up
```

### provisioner

当 AIO provider 同时配置了 `provisioner_url` 时，脚本会额外启动 `provisioner`：

```yaml
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
  provisioner_url: http://provisioner:8002
```

此模式还需要可用的 Kubernetes 配置。更多 provisioner 细节见 [provisioner 说明](../docker/provisioner/README.md)。

## 安全建议

DeerFlow 具备执行工具、读写运行期文件、调用外部资源等能力。生产部署请至少做到：

- 不要直接暴露到公网。
- 使用反向代理、VPN、内网访问或 IP allowlist 控制入口。
- 为 `DEER_FLOW_HOME` 设置合适的文件权限。
- 持久保存并保护 `.better-auth-secret`，避免每次重启生成不同 secret。
- 不在日志、文档、Git 提交中泄露 API key。
- 使用 HTTPS 终止代理保护跨网络访问。
- 定期备份 `DEER_FLOW_HOME`。

## 排障

### Docker daemon 不可用

现象：

```text
Docker daemon is not reachable
```

处理：

```bash
docker info
```

确认 Docker 已启动，当前用户有权限访问 Docker。

### Docker Compose 不可用

现象：

```text
Docker Compose plugin is not available
```

处理：

```bash
docker compose version
```

安装或升级 Docker Compose v2。

### AIO sandbox 找不到 Docker socket

现象：

```text
Docker socket not found
```

处理：

```bash
export DEER_FLOW_DOCKER_SOCKET=/var/run/docker.sock
make up
```

如果服务器的 socket 路径不同，请换成真实路径。也可以改用 local 或 provisioner sandbox 模式。

### 端口被占用

默认端口是 `2026`。如果已被占用：

```bash
export PORT=3026
make up
```

### 依赖下载慢或失败

设置镜像后重试：

```bash
export UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
export NPM_REGISTRY=https://registry.npmmirror.com
make up
```

### 服务启动后页面不可访问

检查容器状态和日志：

```bash
make prod-status
scripts/deploy.sh logs nginx
scripts/deploy.sh logs frontend
scripts/deploy.sh logs gateway
```

如果通过远程服务器访问，请确认防火墙、安全组、反向代理和 `PORT` 配置。

## 快速命令清单

```bash
make prod-config
make up
make prod-status
make prod-logs
make prod-restart
make down
```
