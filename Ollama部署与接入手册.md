# Ollama 本地大模型部署与接入手册

| 项目 | 内容 |
| --- | --- |
| 目标服务器 | AlmaLinux 9.4 / 16 vCPU（Xeon Silver 4309Y）/ 512G 内存 / 无 GPU |
| 推理方式 | 纯 CPU（AVX-512 全支持，VNNI 加速 INT4 推理） |
| 部署目录 | `/ollama/{bin,models,logs}`（独立目录，不污染系统路径） |
| 服务端口 | 11434（systemd 常驻，开机自启） |
| 文档用途 | 从零安装 Ollama → 拉模型 → 接入 DeerFlow → 管理台与 Token 计量 |

---

## 一、架构与定位

```
DeerFlow gateway（Docker 容器）
   │  调用 http://host.docker.internal:11434/v1
   ▼
Ollama（systemd 常驻服务，宿主机）
   │  模型加载进内存（/ollama/models 落盘）
   ▼
CPU 推理（16 vCPU + AVX-512，无需 GPU）
```

**Ollama 的行为逻辑**：`ollama run <模型>` 第一次会从在线仓库下载权重到本地，之后完全本地推理，不走网络、零 API 费用。模型文件在 `/ollama/models/blobs/` 下，删除目录即卸载。

**本机预期推理速度**（实测参考）：

| 模型 | 量化 | 内存占用 | 预估速度 | 适用 |
| --- | --- | --- | --- | --- |
| qwen2.5-coder:7b | Q4 | ~5G | 18-28 tok/s | 函数级改写、写配置，首选 |
| deepseek-coder-v2:16b-lite | Q4 | ~10G | 10-18 tok/s | 质量好一档，稍慢 |
| qwen2.5-coder:32b | Q4 | ~19G | 5-10 tok/s | 跨文件重构，体验一般 |

---

## 二、安装流程（含踩坑实录）

### 2.1 目录规划

```bash
mkdir -p /ollama/{bin,models,logs}
```

### 2.2 下载二进制

> **踩坑实录**（按顺序踩过的路，不要再走）：
> 1. AlmaLinux 默认**没有 wget**，用 curl（`-L` 跟随重定向）
> 2. ModelScope 的 ollama 镜像路径已失效（404）
> 3. hf-mirror.com 上**没有 ollama/ollama 仓库**，下载得到的是 20MB 的纯文本 "Repository not found"（用 `file` 命令可鉴别）
> 4. ghproxy.com 代理 SSL 握手失败（站点不稳定）
> 5. **可用路径：GitHub Release 直连下载 `ollama-linux-amd64.tar.zst`（1.41 GB）**

```bash
# 1. 查最新版本号
LATEST=$(curl -sI "https://github.com/ollama/ollama/releases/latest" \
  | grep -i "^location:" | sed 's|.*/tag/||' | tr -d '\r\n')
echo "Latest: $LATEST"

# 2. 后台下载（nohup 防 SSH 断连；-c 支持断点续传）
nohup wget -c --tries=100 --timeout=30 \
  -O /tmp/ollama.tar.zst \
  "https://github.com/ollama/ollama/releases/download/${LATEST}/ollama-linux-amd64.tar.zst" \
  > /tmp/ollama-wget.log 2>&1 &
echo "下载已启动，PID: $!"
```

**SSH 断连后查进度**（重连后随时执行）：

```bash
# 文件大小（目标 1.41 GB）
ls -lh /tmp/ollama.tar.zst

# 日志尾部（看速度和 ETA）
tail -5 /tmp/ollama-wget.log

# 进度百分比
NOW=$(stat -c %s /tmp/ollama.tar.zst 2>/dev/null || echo 0)
echo "已下载: $NOW 字节"

# 判断是否还在下载
ps aux | grep wget | grep -v grep
```

> **要点**：SSH 断开会杀死前台进程，必须用 `nohup ... &` 后台下载。断点续传：重连后重跑同一条 `wget -c` 命令即可从断点继续，不要删文件。

### 2.3 解压安装

tar.zst 包内含两个目录：`bin/ollama`（主程序）和 `lib/ollama/`（推理后端库，含 ggml-cpu 等）。**官方布局要求 `bin/` 与 `lib/` 平级**——ollama 运行时按「可执行文件所在目录的上一级找 `lib/ollama`」定位后端，`lib` 位置不对会导致 `ollama run` 报 runner 错误。

```bash
# zstd 解压支持（如已装可跳过）
dnf install -y zstd

# 解压到临时目录，再按官方布局归位
tar --zstd -xf /tmp/ollama.tar.zst -C /tmp/ollama-extract
mv /tmp/ollama-extract/bin/ollama /ollama/bin/ollama
mv /tmp/ollama-extract/lib /ollama/lib
chmod +x /ollama/bin/ollama
rm -rf /tmp/ollama-extract /tmp/ollama.tar.zst

# 验证
/ollama/bin/ollama --version
# 期望输出：ollama version is 0.17.x（或更新）
```

**归位后的目录结构**（`bin/` 与 `lib/` 必须平级）：

```
/ollama/
├── bin/ollama        ← 可执行文件
├── lib/ollama/       ← 推理后端库（cuda/rocm 库用不上也不要删）
├── models/
└── logs/
```

> **易错点**：若直接解压到 `/ollama/bin/`，会得到 `/ollama/bin/bin/ollama` 和 `/ollama/bin/lib/ollama`。手动把 `ollama` 移到 `/ollama/bin/` 后，还需把 `lib` 移到 `/ollama/lib`（与 bin 平级），否则启动模型时报 runner 相关错误。

### 2.4 systemd 服务（自定义路径 + CPU 隔离最终版）

```bash
cat > /etc/systemd/system/ollama.service <<'EOF'
[Unit]
Description=Ollama LLM Service
After=network.target

[Service]
Type=simple
Environment="HOME=/root"
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_MODELS=/ollama/models"
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
ExecStart=/usr/bin/taskset -c 0-5 /ollama/bin/ollama serve
Restart=always
RestartSec=5
Nice=10

[Install]
WantedBy=multi-user.target
EOF

kill -HUP 1
systemctl enable ollama
systemctl start ollama
systemctl status ollama --no-pager
```

**期望状态**：`active (running)`，日志含 `Listening on [::]:11434`。

> **为什么必须 systemd**：`ollama serve &` 这种后台方式在 SSH 断开时会被杀掉（实际踩过：进程消失、curl 拿到空响应）。systemd 提供崩溃自动拉起 + 开机自启。

> **本机 systemd 实测坑**（StoreDB02，AlmaLinux 9.4）：
> 1. **`systemctl daemon-reload` 报 `Unknown command verb`**——systemctl 不支持该 verb（版本老或被包装）。等价替代：`kill -HUP 1`（PID 1 的 systemd 收到 SIGHUP 即重载全部 unit 文件）。新写/改过 unit 文件后必须执行，否则服务识别不到。
> 2. **必须显式设置 `Environment="HOME=/root"`**——这台 systemd 启动服务时不自动设置 HOME，ollama serve 启动即报 `Error: $HOME is not defined` 退出（status=1），陷入无限重启循环（`activating (auto-restart)`）。日志可用 `journalctl -u ollama -n 20 --no-pager` 查看。
> 3. **二进制不在 PATH**——装在 `/ollama/bin/` 下直接敲 `ollama` 报 command not found；软链到 `/usr/local/bin` 后**仍然**找不到（这台 root 的 PATH 里没有 `/usr/local/bin`）。正确做法：`ln -s /ollama/bin/ollama /usr/bin/ollama`（`/usr/bin` 一定在 PATH）。
>
> unit 中 `taskset`/`KEEP_ALIVE`/`NUM_PARALLEL` 等参数的完整说明见第五章。

---

## 三、模型管理

### 3.1 在线拉取（首选尝试）

```bash
# 7B 代码模型（约 4.7G，首选）
ollama pull qwen2.5-coder:7b

# 16B 代码模型（约 10G，质量更好）
ollama pull deepseek-coder-v2:16b-lite

# 拉完即本地化，验证
ollama list
ls -lh /ollama/models/blobs/
```

> `ollama pull` 走 registry.ollama.ai（Cloudflare CDN）。**本机实测该域名 443 完全不通**（`dial tcp 104.18.17.170:443: i/o timeout`，与 GitHub 直连可用形成对比），在线拉取在这台服务器上不可用，直接走 3.2 手动导入。

### 3.2 手动导入 GGUF（本机实际采用的路径）

从国内可访问的 HF 镜像下载 GGUF 权重，用 Modelfile 导入（nohup 后台下载防 SSH 断连，`-C -` 断点续传，7B 权重约 4.7G）：

```bash
# 1. 后台下载 GGUF（hf-mirror 是 HuggingFace 完整镜像，Qwen 官方 GGUF 仓库真实存在）
nohup curl -L -C - --connect-timeout 15 \
  "https://hf-mirror.com/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/qwen2.5-coder-7b-instruct-q4_k_m.gguf" \
  -o /ollama/models/qwen2.5-coder-7b-q4_k_m.gguf \
  > /tmp/gguf-download.log 2>&1 &

# 查进度：ls -lh 目标约 4.7G；tail /tmp/gguf-download.log 看速度
ls -lh /ollama/models/qwen2.5-coder-7b-q4_k_m.gguf

# 2. 写 Modelfile
cat > /tmp/qwen-coder.Modelfile <<'EOF'
FROM /ollama/models/qwen2.5-coder-7b-q4_k_m.gguf
EOF

# 3. 导入
ollama create qwen2.5-coder:7b -f /tmp/qwen-coder.Modelfile

# 4. 验证
ollama list
```

### 3.3 常用模型命令

| 命令 | 作用 |
| --- | --- |
| `ollama list` | 列出本地已有模型 |
| `ollama run <模型> "问题"` | 对话测试（首次自动拉取） |
| `ollama ps` | 查看当前加载进内存的模型 |
| `ollama show <模型>` | 查看模型详情 |
| `ollama rm <模型>` | 删除本地模型 |
| `ollama stop <模型>`（新版） | 从内存卸载模型 |

---

## 四、部署验证

```bash
# 1. 服务状态
systemctl status ollama --no-pager | head -5

# 2. 端口监听
ss -tlnp | grep 11434
# 期望：LISTEN ... *:11434

# 3. CLI 推理测试（感受速度）
ollama run qwen2.5-coder:7b "写一个 Python fibonacci 函数"

# 4. API 测试（DeerFlow 用的就是这个端点）
curl -s http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen2.5-coder:7b","messages":[{"role":"user","content":"hello"}]}'
```

> API 响应中 `usage` 字段含 `prompt_tokens` / `completion_tokens`——这是 Ollama 提供的唯一 token 数据（见第七章的计量方案）。

---

## 五、CPU 资源隔离与调优（必做，否则挤死业务）

### 5.1 问题：推理默认抢光全部 16 vCPU

Ollama 推理线程数默认 = 全部核数。实测接 DeerFlow 后发起对话，llama-server 进程冲到 **1582%（≈16 核全开）**，同机的 DeerFlow gateway、Postgres、dockerd 全部被饿死。

**先澄清一个认知**：CPU 满载是 CPU 推理的**正常状态**（推理就是 CPU 矩阵运算），不是故障。内存只是存放模型权重的仓库（7B Q4 仅 ~5G），"用内存替代 CPU"在原理上不存在。调优目标不是"少用 CPU"，而是**限定 Ollama 用的核数、并把调度优先级让给业务**。内存唯一能帮忙的姿势是让模型常驻（省冷加载），512G 内存下毫无压力。

### 5.2 方案：taskset 绑核（替代失效的 CPUQuota）

| 参数 | 作用 |
| --- | --- |
| `ExecStart=/usr/bin/taskset -c 0-5 ...` | **绑核到 0-5（6 核）**，llama-server 子进程自动继承，无论推理多猛最多占 6 核，剩 10 核永远留给业务 |
| `Nice=10` | 调度优先级让位，业务进程抢核永远优先 |
| `OLLAMA_KEEP_ALIVE=-1` | **模型永久常驻内存**，不自动卸载（默认 5 分钟无请求即卸载，下次请求要冷加载几十秒） |
| `OLLAMA_NUM_PARALLEL=1` | 同时只处理 1 个请求，超出排队，避免并发时 CPU 叠加倍增 |
| `OLLAMA_MAX_LOADED_MODELS=1` | 内存中最多 1 个模型 |

> **为什么不用 systemd 的 `CPUQuota=600%`**：在这台机器上实测**静默失效**（unit 写了、restart 也执行了，llama-server 照样 1582%）。根因：CPUQuota 依赖 cpu cgroup controller，该机器未挂载此 controller；而 `Nice` 是普通系统调用（setpriority）不依赖 cgroup，所以生效了（top 里 NI=10 可见）。**鉴别方法**：unit 里同时写 Nice 和 CPUQuota，top 里 Nice 生效而 %CPU 超限，即是此坑。`taskset` 用 CPU 亲和性（sched_setaffinity）系统调用，同样绕过 cgroup，实测必生效。

> **Ollama 0.17 架构注意**：推理引擎是独立子进程 `llama-server`（`ps` 可见 `--model ... --port` 参数），systemd 的环境变量/Nice/绑核都会传递给子进程，无需单独配置。

### 5.3 验证隔离生效

```bash
# 1. 确认 llama-server 的 CPU 亲和性（最直接的证据）
taskset -pc $(pgrep -f llama-server)
# 期望输出：current affinity list: 0-5

# 2. DeerFlow 发起对话的同时，看推理进程 CPU
top -bn1 -p $(pgrep -f llama-server) | tail -2
# 推理中 %CPU 应稳定 ≤ 600%

# 3. 确认业务进程正常
top -bn1 | head -15
# gateway/java/postgres 应有正常 CPU 余量，不再长时间 wait
```

### 5.4 Trade-off 与微调

- 绑 6 核后推理速度约 **10-15 tok/s**（全核 16 核约 18-28 tok/s），单条回复变慢但仍在"比人阅读快"的水平
- 比 CPUQuota 限流更高效：绑核没有 throttle 停顿，6 核全速跑
- 核数可按观察调整：改 `taskset -c 0-5` 为 `0-3`（4 核，更保守）或 `0-7`（8 核，更快），改完 `kill -HUP 1 && systemctl restart ollama`
- 7B 模型常驻内存 ~5G；将来加 32B 模型常驻约 19G，512G 内存下均可忽略

---

## 六、接入 DeerFlow

### 6.1 config.yaml 增加模型配置

在 `models:` 列表**末尾追加**（不替换现有条目），完整字段如下：

```yaml
models:
  # ...现有的 kimi-for-coding 等配置保留不动...
  - name: qwen-coder-local-7b
    display_name: Qwen2.5-Coder-7B (Local CPU)
    use: deerflow.models.patched_deepseek:PatchedChatDeepSeek
    model: qwen2.5-coder:7b
    api_base: http://host.docker.internal:11434/v1
    api_key: ollama
    max_tokens: 32768
    supports_vision: false
```

**每个字段都必须有**，尤其 `use`——DeerFlow 启动时用 pydantic 严格校验 models 配置，缺 `use` 会直接报 `1 validation error for AppConfig: models.N.use Field required`，gateway 启动失败（实测踩过）。`use` 的取值与现有 kimi-for-coding 相同：该类走 OpenAI 兼容协议，Ollama 的 `/v1` 端点完全兼容。

> **关键点**：gateway 跑在 Docker 容器里，`localhost` 指向容器自身而非宿主机，必须用 `host.docker.internal`。`model:` 字段的值必须与服务器上 `ollama list` 显示的模型名逐字一致（含冒号后缀）。

### 6.2 docker-compose-live.yaml 增加 host 映射

gateway 服务下追加：

```yaml
gateway:
  extra_hosts:
    - "host.docker.internal:host-gateway"
```

### 6.3 重启生效

```bash
cd /opt/deer-flow
git pull   # 如果配置文件进了仓库
docker compose --env-file .env -p deer-flow-live -f docker/docker-compose-live.yaml restart gateway
```

之后在 DeerFlow 设置 → 模型中即可看到本地模型。

---

## 七、管理台与 Token 计量

### 7.1 结论速览

| 能力 | Ollama 原生 | 补足方案 |
| --- | --- | --- |
| Web 管理台 | ❌ 仅 CLI + API | **Open WebUI**（对话界面、模型管理、用户管理） |
| 单次请求 token 数 | ✅ API 响应 `usage` 字段 | — |
| **持久化用量报表**（每个调用方用了多少） | ❌ **完全不做记录** | **LiteLLM Proxy**（per-key/per-user 计量 + 管理后台） |
| DeerFlow 内的用量展示 | — | DeerFlow 自带 token 统计（消息级），但仅限本系统调用 |

### 7.2 安装 Open WebUI（Docker，一条命令）

```bash
docker run -d \
  --name open-webui \
  --restart=always \
  -p 3001:8080 \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -v open-webui:/app/backend/data \
  --add-host=host.docker.internal:host-gateway \
  ghcr.io/open-webui/open-webui:main
```

访问 `http://<服务器IP>:3001`，首次注册的账号即管理员。功能：对话测试模型、管理模型、多用户、对话历史。

> 防火墙需放行 3001：`firewall-cmd --permanent --add-port=3001/tcp && firewall-cmd --reload`

### 7.3 LiteLLM Proxy（多调用方 token 计量，100 人规模推荐）

DeerFlow 只是接入方之一，后续还会有其他系统接模型。标准做法是加一层 LiteLLM 网关统一记账：

```
各调用系统（DeerFlow / 其他业务）
   │  统一走 OpenAI 兼容协议
   ▼
LiteLLM Proxy（记账：谁、用了哪个模型、多少 token）
   ├── 本地 Ollama（免费）
   └── 云 API（DeepSeek / Kimi，按量付费）
```

```bash
# Docker 部署 LiteLLM（管理后台 + 代理一体）
docker run -d \
  --name litellm \
  --restart=always \
  -p 4000:4000 \
  -e STORE_MODEL_IN_DB=True \
  -v litellm-config:/app/config \
  ghcr.io/berriai/litellm:main-latest \
  --config /app/config/config.yaml
```

`config.yaml` 示例（本地模型 + 云 API 统一入口）：

```yaml
model_list:
  - model_name: local-coder
    litellm_params:
      model: ollama/qwen2.5-coder:7b
      api_base: http://host.docker.internal:11434
  - model_name: deepseek
    litellm_params:
      model: deepseek/deepseek-chat
      api_key: os.environ/DEEPSEEK_API_KEY

general_settings:
  master_key: sk-xxxx        # 管理后台登录密钥
```

部署后在 `http://<IP>:4000/ui` 管理后台：给每个接入系统发独立 API Key → 后台报表可见每个 Key 的请求次数、token 消耗、费用折算。**这正是"每个接进来的用了多少 token"的完整答案。**

> 若暂时只有 DeerFlow 一个接入方，可先不装 LiteLLM——DeerFlow 自身的消息 token 统计够用；等第二个系统接入时再上网关。

---

## 八、日常运维速查

| 操作 | 命令 |
| --- | --- |
| 服务状态 | `systemctl status ollama` |
| 重启服务 | `systemctl restart ollama` |
| 查看服务日志 | `journalctl -u ollama -f` |
| 查看已加载模型 | `ollama ps` |
| **查看推理进程绑核** | `taskset -pc $(pgrep -f llama-server)`（期望 `0-5`） |
| **查看推理实时 CPU** | `top -bn1 -p $(pgrep -f llama-server) \| tail -2`（应 ≤600%） |
| 升级 Ollama | 重新下载新版 tar.zst 解压覆盖 `/ollama/bin/ollama` 后 `systemctl restart ollama` |
| 模型存储位置 | `/ollama/models/blobs/`（占满盘时清理：`ollama rm` 不用的模型） |
| 模型常驻策略 | unit 里 `OLLAMA_KEEP_ALIVE=-1` 永久常驻；改为 `30m` 表示 30 分钟无请求后卸载释放内存 |

**常见故障**：

| 症状 | 根因与处理 |
| --- | --- |
| `curl 11434` 空响应 / JSON 解析报错 | ollama 进程没活着。`systemctl status ollama` → `systemctl restart ollama` |
| 服务 `activating (auto-restart)` 无限重启 | 启动即崩。`journalctl -u ollama -n 20` 看根因——`$HOME is not defined` 则补 `Environment="HOME=/root"`；端口占用则 `pkill -f ollama` 清残留进程再 start |
| `ollama pull` 报 registry 443 timeout | 本机出口不通 registry.ollama.ai，走 3.2 GGUF 手动导入 |
| `ollama pull` 报 403 / 手动导入后 `ollama create` 找不到模型 | `ollama list` 核对模型名逐字一致（含 `:7b` 冒号后缀） |
| **推理时 16 核全满、业务卡顿** | unit 未加 taskset 绑核，或 CPUQuota 写了但本机 cpu cgroup controller 缺失静默失效（见 5.2）。用 taskset 方案 |
| DeerFlow 调不通 11434 | 容器内 `localhost` 不指向宿主机，确认用了 `host.docker.internal` + compose 加了 `extra_hosts` |
| DeerFlow 启动报 `models.N.use Field required` | config.yaml 模型条目缺 `use` 字段（见 6.1） |
| 下载的"二进制"执行报错 | `file /ollama/bin/ollama` 鉴别——输出非 `ELF 64-bit` 说明下到的是错误页文本，换下载源 |
| `ollama: command not found` | PATH 问题，软链到 `/usr/bin`：`ln -s /ollama/bin/ollama /usr/bin/ollama`（`/usr/local/bin` 在这台机器的 PATH 之外） |

---

## 九、版本记录

| 日期 | 变更 |
| --- | --- |
| 2026-10-23 | 首版：安装（GitHub Release tar.zst 路径）、systemd 自定义路径部署、模型拉取与 GGUF 导入、DeerFlow 接入、Open WebUI 与 LiteLLM 计量方案 |
| 2026-10-26 | CPU 资源隔离方案落地（第五章）：taskset 绑核 0-5 替代本机静默失效的 CPUQuota；KEEP_ALIVE=-1 / NUM_PARALLEL=1 常驻与并发调优；GGUF 手动导入转正为默认路径（3.2）；systemd 三坑补全（daemon-reload / HOME / PATH）；DeerFlow 接入补 `use` 字段坑（6.1）；故障表扩至 9 条 |
