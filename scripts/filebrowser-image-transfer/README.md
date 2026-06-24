# DeerFlow 镜像离线中转

用于无法让生产服务器直接访问镜像仓库的场景：

```text
测试服务器 Docker
  -> 复制到测试 FileBrowser 数据目录
  -> 本地电脑
  -> 生产 FileBrowser
  -> 生产服务器 Docker
```

三支脚本按顺序执行：

1. `01-test-package-upload.sh`：测试机打包并复制镜像到本机 FileBrowser 数据目录。
2. `02-local-relay.sh`：本地从测试 FileBrowser 下载，再上传到生产 FileBrowser。
3. `03-prod-download-load.sh`：生产机通过 SCP 拉取、校验、导入并可选部署。

每次传输包含前端镜像、后端镜像和 SHA-256 校验清单。账号密码只通过环境变量传入，不写入脚本或 Git。

## 前置条件

- 三台机器均需安装 Bash、gzip；本地和生产机需要 curl。
- 测试机和生产机需安装 Docker。
- 两套 FileBrowser 中需提前创建远端目录，默认是 `deer-flow`。
- 测试机需能写入 `/opt/file/data/deer-flow`。
- FileBrowser 需提供 `/api/login`、`/api/tus` 和 `/api/raw` 接口供 02 使用。
- 生产服务器需能通过 SCP 访问 `172.20.62.36`。

先赋予执行权限：

```bash
chmod +x scripts/filebrowser-image-transfer/*.sh
```

## 1. 测试服务器：打包并复制

```bash
export DEER_FLOW_IMAGE_TAG=v0.3.0
export FILEBROWSER_DATA_DIR=/opt/file/data/deer-flow

./scripts/filebrowser-image-transfer/01-test-package-upload.sh
```

01 不再登录 FileBrowser，而是直接执行等价于以下操作的本机复制：

```bash
cp /tmp/deer-flow-image-transfer/v0.3.0/* /opt/file/data/deer-flow/
```

复制时先写入目标目录中的 `.part` 文件，完成后再改为正式文件名，默认权限为 `0644`。

默认打包以下已有镜像：

```text
ghcr.io/xlows-1227/deer-flow-frontend:${DEER_FLOW_IMAGE_TAG}
ghcr.io/xlows-1227/deer-flow-backend:${DEER_FLOW_IMAGE_TAG}
```

如果希望脚本先从当前仓库构建生产镜像：

```bash
export BUILD_IMAGES=1
export DOCKER_BUILD_PLATFORM=linux/amd64
./scripts/filebrowser-image-transfer/01-test-package-upload.sh
```

如果测试服务器上已存在同一标签的前端包、后端包和校验清单，01 会提示：

```text
是否直接复制已有文件到 FileBrowser 数据目录？[y/N/r(重新打包)]
```

- `y`：校验已有文件后直接复制，不再执行 `docker save`。
- `n` 或回车：取消发布。
- `r`：覆盖已有文件，重新打包后复制。

无人值守运行可设置：

```bash
export EXISTING_PACKAGE_ACTION=upload     # 复制已有文件
export EXISTING_PACKAGE_ACTION=repackage  # 重新打包
export EXISTING_PACKAGE_ACTION=abort      # 取消
```

`BUILD_IMAGES=1` 始终优先执行重新构建和打包。

## 2. 本地电脑：从测试中转到生产

```bash
export DEER_FLOW_IMAGE_TAG=v0.3.0

export TEST_FILEBROWSER_URL=http://test-filebrowser.example:8001
export TEST_FILEBROWSER_USERNAME=admin
read -rsp "测试 FileBrowser 密码: " TEST_FILEBROWSER_PASSWORD
export TEST_FILEBROWSER_PASSWORD
echo

export PROD_FILEBROWSER_URL=http://prod-filebrowser.example:8001
export PROD_FILEBROWSER_USERNAME=admin
read -rsp "生产 FileBrowser 密码: " PROD_FILEBROWSER_PASSWORD
export PROD_FILEBROWSER_PASSWORD
echo

./scripts/filebrowser-image-transfer/02-local-relay.sh
```

02 会记录整个流程的开始时间、结束时间和总耗时，远端执行 01、下载及上传时间都包含在内。成功或失败退出时都会显示：

```text
02 本地中转执行成功
开始时间: 2026-06-23 15:30:00
结束时间: 2026-06-23 15:42:18
总耗时:   00:12:18
```

02 默认并发传输两份大文件：

- 前端镜像和后端镜像并发从测试 FileBrowser 下载。
- 校验通过后，前端镜像和后端镜像并发上传生产 FileBrowser。
- SHA-256 清单单独处理，并在两份镜像上传完成后最后上传。

如果网络或 FileBrowser 不适合并发，可关闭：

```bash
export PARALLEL_TRANSFERS=0
```

远端 01 也默认并发执行前端、后端的 `docker save | gzip`。如果测试服务器磁盘性能较弱，并发反而更慢，可关闭：

```bash
export TEST_REMOTE_PARALLEL_IMAGE_PACKAGING=0
```

02 会分别输出这些阶段的耗时，便于确认瓶颈：

- 测试服务器执行 01。
- 测试 FileBrowser 下载到本地。
- 本地 SHA-256 与 gzip 校验。
- 上传生产 FileBrowser。

当需要从测试环境重新拉取时，02 会先使用 `expect`：

1. 登录堡垒机。
2. 在 `Opt>` 输入测试服务器地址。
3. 执行 `sudo su -` 切换到 root 账号。
4. 以 root 身份执行测试服务器上的 `01-test-package-upload.sh`。
5. 等待 01 完成镜像打包并复制到 `/opt/file/data/deer-flow`。
6. 下载到本地，再上传生产 FileBrowser。

需要配置：

```bash
export BASTION_HOST=61.155.145.200
export BASTION_PORT=52222
export BASTION_USER=你的账号
read -rsp "堡垒机密码: " BASTION_PASS
export BASTION_PASS
echo

# 堡垒机仅支持旧 ssh-rsa 主机密钥时启用
export BASTION_ENABLE_LEGACY_SSH_RSA=1

export TEST_TARGET_IP=10.218.221.161
export TEST_REMOTE_SCRIPT_PATH=/opt/deer-flow/scripts/filebrowser-image-transfer/01-test-package-upload.sh
export TEST_REMOTE_FILEBROWSER_DATA_DIR=/opt/file/data/deer-flow
```

远端测试服务器必须已有 01 脚本。若暂时不希望远程执行 01，可设置：

```bash
export RUN_TEST_REMOTE_PACKAGE=0
```

中转文件默认保留在 `${TMPDIR:-/tmp}/deer-flow-image-transfer/<tag>`。如果同一标签对应的前端包、后端包和校验清单都已存在，脚本会询问：

```text
是否重新从测试环境拉取？[y/N]
```

- 输入 `y`：覆盖本地文件，重新从测试 FileBrowser 下载。
- 输入 `n` 或直接回车：跳过测试环境，校验本地文件后直接上传生产 FileBrowser。
- 文件缺失或为空：无需询问，自动从测试环境下载。

无人值守运行可通过环境变量控制：

```bash
# 强制重新下载
export REDOWNLOAD_EXISTING=1

# 始终复用已有文件
export REDOWNLOAD_EXISTING=0
```

成功后自动清理可设置：

```bash
export CLEAN_LOCAL_AFTER_UPLOAD=1
```

## 3. 生产服务器：SCP 拉取、导入及部署

```bash
export DEER_FLOW_IMAGE_TAG=v0.3.0
./scripts/filebrowser-image-transfer/03-prod-download-load.sh
```

默认执行等价于：

```bash
scp root@172.20.62.36:/opt/tools/file_browser/deer-flow/deer-flow-images-v0.3.0.sha256 ./
scp root@172.20.62.36:/opt/tools/file_browser/deer-flow/deer-flow-frontend-v0.3.0.tar.gz ./
scp root@172.20.62.36:/opt/tools/file_browser/deer-flow/deer-flow-backend-v0.3.0.tar.gz ./
```

文件默认下载到执行 03 时的当前目录。下载后会校验 SHA-256 和 gzip 完整性，然后询问：

```text
是否解压并导入 Docker 镜像？ [y/N]
```

选择 `y` 后执行：

```bash
gunzip -c deer-flow-frontend-v0.3.0.tar.gz | docker load
gunzip -c deer-flow-backend-v0.3.0.tar.gz | docker load
```

导入完成后继续询问：

```text
镜像已导入，是否立即部署 DeerFlow？ [y/N]
```

选择 `y` 后进入 `/opt/deer-flow` 并执行：

```bash
cd /opt/deer-flow
git pull
export DEER_FLOW_IMAGE_TAG=v0.3.0
docker compose --env-file .env -p deer-flow \
  -f docker/docker-compose.yaml \
  -f docker/docker-compose.images.yaml \
  up -d --no-build frontend gateway nginx
```

无人值守运行：

```bash
export LOAD_IMAGES_ACTION=1
export DEPLOY_AFTER_LOAD=1
./scripts/filebrowser-image-transfer/03-prod-download-load.sh
```

## 常用覆盖变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `IMAGE_REGISTRY` | `ghcr.io/xlows-1227` | 镜像仓库前缀 |
| `FILEBROWSER_REMOTE_DIR` | `deer-flow` | 02 使用的 FileBrowser 目录 |
| `FILEBROWSER_DATA_DIR` | `/opt/file/data/deer-flow` | 01 直接复制文件的本地目录 |
| `FILEBROWSER_FILE_MODE` | `0644` | 01 复制后设置的文件权限 |
| `FILEBROWSER_DATA_OWNER` | 空 | 01 可选设置的目标文件属主，例如 `filebrowser:filebrowser` |
| `TRANSFER_WORK_DIR` | `/tmp/deer-flow-image-transfer/<tag>` | 临时目录 |
| `GZIP_LEVEL` | `1` | 测试机压缩级别，1 最快、9 最小 |
| `EXISTING_PACKAGE_ACTION` | `ask` | 01 发现已有完整打包文件时的处理方式 |
| `FILEBROWSER_INSECURE` | `0` | 是否忽略服务器脚本的 HTTPS 证书错误 |
| `FILEBROWSER_CA_CERT` | 空 | 自定义 CA 证书路径 |
| `FILEBROWSER_LOGIN_RETRIES` | `3` | 登录发生连接重置等传输错误时的尝试次数 |
| `FILEBROWSER_RETRY_DELAY` | `2` | 登录重试等待秒数 |
| `FILEBROWSER_LOGIN_TIMEOUT` | `60` | 单次登录请求最长秒数 |
| `REDOWNLOAD_EXISTING` | `ask` | 本地已有完整文件时询问、强制重拉或直接复用 |
| `PARALLEL_TRANSFERS` | `1` | 02 是否并发下载和上传前后端镜像 |
| `PARALLEL_IMAGE_PACKAGING` | `1` | 01 是否并发导出、压缩前后端镜像 |
| `RUN_TEST_REMOTE_PACKAGE` | `1` | 重新拉取前是否通过 expect 在测试机执行 01 |
| `BASTION_ENABLE_LEGACY_SSH_RSA` | `1` | 兼容只提供 ssh-rsa 主机密钥的旧堡垒机 |
| `TEST_REMOTE_SCRIPT_PATH` | `/opt/deer-flow/.../01-test-package-upload.sh` | 测试服务器上的 01 脚本绝对路径 |
| `TEST_REMOTE_FILEBROWSER_DATA_DIR` | `/opt/file/data/deer-flow` | 02 传给远端 01 的复制目录 |
| `TEST_REMOTE_TIMEOUT` | `7200` | 远端打包并复制的最长等待秒数 |
| `TEST_REMOTE_EXISTING_PACKAGE_ACTION` | `repackage` | 02 调用远端 01 时如何处理测试机已有文件 |
| `TEST_REMOTE_PARALLEL_IMAGE_PACKAGING` | `1` | 02 是否要求远端 01 并发打包 |
| `SCP_HOST` | `172.20.62.36` | 03 拉取文件的服务器 |
| `SCP_USER` | `root` | 03 使用的 SCP 用户 |
| `SCP_REMOTE_DIR` | `/opt/tools/file_browser/deer-flow` | 03 的远端文件目录 |
| `LOAD_IMAGES_ACTION` | `ask` | 03 是否执行 `gunzip -c ... \| docker load` |
| `DEPLOY_AFTER_LOAD` | `ask` | 镜像导入后是否立即部署 |
| `DEER_FLOW_DEPLOY_DIR` | `/opt/deer-flow` | DeerFlow 生产部署目录 |

完整配置示例见 `env.example`。不要提交包含真实密码的环境文件。
