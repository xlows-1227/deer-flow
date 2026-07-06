# LDAP 内网账户登录配置指南

DeerFlow 支持接入企业内网的 LDAP / Active Directory 进行账户登录。本文档面向需要把 DeerFlow 接入公司目录服务（如 YumChina 内网 AD）的运维人员。

> 相关设计背景见 [AUTH_DESIGN.md](AUTH_DESIGN.md)。本功能在原本地登录基础上扩展，不影响已有账户。

---

## 1. 登录策略概览

启用 LDAP 后，登录按以下规则**严格分派**：

| 账户类型 | 登录方式 | 说明 |
|----------|----------|------|
| 管理员账户（`AUTH_LDAP_LOCAL_ADMIN_EMAIL` 指定，默认 `admin@yumchina.com`） | 本地数据库密码 | 即使 LDAP 宕机也能进后台运维 |
| 其余所有账户 | LDAP 校验（严格模式） | **LDAP 失败即拒绝，不回退本地密码**，避免用本地弱密码绕过目录策略 |

**关键设计：**

- 登录输入 **sAMAccountName**（短用户名，如 `john`），**不含** `@yumchina.com`；
- LDAP 用户首次登录时，会在本地 `users` 表自动创建一条**影子记录**（`password_hash=NULL`、`oauth_provider="ldap"`），用于后续 JWT 身份识别，对既有 thread/run/feedback 数据零侵入；
- LDAP 用户的密码必须通过企业目录自助修改，DeerFlow 的「修改密码」接口会拒绝 LDAP 用户；
- `AUTH_LDAP_ENABLED=false`（或不设置）时，LDAP 完全关闭，所有账户走本地数据库（即默认行为）。

---

## 2. 配置项一览

所有 LDAP 配置通过**环境变量**设置，写入项目根目录的 `.env` 文件即可。

| 环境变量 | 必填 | 默认值 | 说明 |
|----------|------|--------|------|
| `AUTH_LDAP_ENABLED` | 是 | `false` | LDAP 总开关。`true`/`1`/`yes`/`on`（不区分大小写）均视为启用 |
| `AUTH_LDAP_URL` | 是（启用时） | — | LDAP 服务器地址，如 `ldap://ldap.yumchina.com` |
| `AUTH_LDAP_BASE` | 是（启用时） | — | 搜索根 DN，如 `ou=YumChina,DC=cn,DC=YumChina,DC=com` |
| `AUTH_LDAP_BIND_USERNAME` | 是（启用时） | — | 服务账户用户名（用于搜索 bind） |
| `AUTH_LDAP_BIND_PASSWORD` | 是（启用时） | — | 服务账户密码 |
| `AUTH_LDAP_OBJECTCLASS` | 否 | `sAMAccountName` | 用户唯一标识属性。百胜员工与外部员工混用时用 `sAMAccountName` |
| `AUTH_LDAP_ATTR_REALNAME` | 否 | `givenName` | 名字对应的 LDAP 属性 |
| `AUTH_LDAP_ATTR_SN` | 否 | `sn` | 姓对应的 LDAP 属性 |
| `AUTH_LDAP_ATTR_EMAIL` | 否 | `mail` | 邮箱对应的 LDAP 属性 |
| `AUTH_LDAP_LOCAL_ADMIN_EMAIL` | 否 | `admin@yumchina.com` | 始终走本地密码的管理员邮箱（输入完整邮箱或裸用户名均可匹配） |
| `AUTH_LDAP_DOMAIN` | 否 | `@yumchina.com` | LDAP 条目无 `mail` 属性时拼接的邮箱域名（自动补 `@` 前缀） |

> **容错提示：** `AUTH_LDAP_BASE` 支持 `DC =com` 这种带空格的写法（取自旧 Spring 配置），后端会自动规范化为 `DC=com`。

---

## 3. 完整配置示例

### YumChina 内网 AD（当前生效配置）

在项目根目录 `.env` 文件中加入：

```bash
# ── LDAP 内网账户登录 ──────────────────────────────────────────────────────
AUTH_LDAP_ENABLED=true
AUTH_LDAP_URL=ldap://ldap.yumchina.com
AUTH_LDAP_BASE=ou=YumChina,DC=cn,DC=YumChina,DC=com
AUTH_LDAP_BIND_USERNAME=serv-it-StarRocks
AUTH_LDAP_BIND_PASSWORD=Yumc!0906
AUTH_LDAP_OBJECTCLASS=sAMAccountName
AUTH_LDAP_ATTR_REALNAME=givenName
AUTH_LDAP_ATTR_SN=sn
AUTH_LDAP_ATTR_EMAIL=mail
AUTH_LDAP_LOCAL_ADMIN_EMAIL=admin@yumchina.com
AUTH_LDAP_DOMAIN=@yumchina.com
```

### 关闭 LDAP（回退到纯本地登录）

```bash
AUTH_LDAP_ENABLED=false
# 或直接删除/注释掉所有 AUTH_LDAP_* 变量
```

---

## 4. 认证流程详解

LDAP 认证采用**两阶段 bind**模式（与服务账户 bind + 用户 bind 的 Spring 配置一致）：

```
用户输入 (sAMAccountName + 密码)
         │
         ▼
┌─────────────────────────────────────────────┐
│ 阶段 1：服务账户 bind + 搜索                  │
│  - 用 AUTH_LDAP_BIND_USERNAME/PASSWORD 绑定   │
│  - 在 BASE 下搜索 (objectclass=<username>)    │
│  - 取得用户 DN 和 mail/givenName/sn 属性      │
└─────────────────────────────────────────────┘
         │  找到唯一用户 DN
         ▼
┌─────────────────────────────────────────────┐
│ 阶段 2：用户 DN + 输入密码 二次 bind          │
│  - bind 成功 → 密码正确                       │
│  - bind 失败 → 密码错误，返回 None            │
└─────────────────────────────────────────────┘
         │  认证成功
         ▼
┌─────────────────────────────────────────────┐
│ 影子用户管理                                  │
│  - 本地表已有该 LDAP 用户 → 按需刷新邮箱       │
│  - 不存在 → 创建影子记录（无密码哈希）          │
│  - 邮箱冲突 → 回退用 <username>@<domain>       │
└─────────────────────────────────────────────┘
         │
         ▼
   签发 JWT，写入 HttpOnly Cookie
```

**安全边界：**

- 任何 LDAP 异常（网络不通、目录多匹配、bind 报错）都会被吞掉并返回 `invalid_credentials`，**不泄露**具体是 LDAP 还是本地失败；
- 目录中存在多条匹配时**拒绝猜测**，需运维修复目录或收窄 `AUTH_LDAP_BASE`；
- LDAP 调用是同步阻塞的，后端用 `asyncio.to_thread` 包装，不会阻塞事件循环。

---

## 5. 登录接口

### 推荐入口：`POST /api/v1/auth/login`

统一 JSON 接口，后端自动按策略分派到本地管理员或 LDAP。

```http
POST /api/v1/auth/login
Content-Type: application/json

{
  "username": "john",
  "password": "你的内网密码"
}
```

- `username` 支持裸 sAMAccountName（`john`）或完整邮箱（`john@yumchina.com`，会自动去掉域名再查 LDAP）；
- 管理员标识（`admin@yumchina.com` 或 `admin`）始终走本地密码；
- 成功返回 `LoginResponse`，JWT 写入 HttpOnly Cookie。

### 向后兼容：`POST /api/v1/auth/login/local`

表单编码接口（`application/x-www-form-urlencoded`），同样按策略分派，供旧客户端/脚本使用。

```http
POST /api/v1/auth/login/local
Content-Type: application/x-www-form-urlencoded

username=john&password=你的内网密码
```

> 两个接口都已加入 CSRF 免检名单和 Auth 中间件公开路径，可在无会话状态下访问。

---

## 6. 本地表影子用户说明

LDAP 用户在本地 `users` 表的字段映射：

| 字段 | 取值 |
|------|------|
| `email` | LDAP `mail` 属性；无则 `<username>@<domain>`；冲突则回退 |
| `password_hash` | `NULL`（LDAP 用户不存本地密码） |
| `system_role` | `user`（LDAP 用户不能自授 admin） |
| `oauth_provider` | `ldap`（标记为 LDAP 影子账户） |
| `oauth_id` | sAMAccountName（如 `john`） |

**邮箱同步：** 若 LDAP 中用户的 `mail` 变了，下次登录会自动刷新本地影子记录的 email（前提是新邮箱未被其他账户占用）。

---

## 7. 常见问题排查

| 现象 | 可能原因 / 处理 |
|------|----------------|
| 所有非 admin 用户都登录失败 | 1) LDAP 服务不可达；2) 服务账户密码过期；3) `AUTH_LDAP_BASE` 错误。查后端日志中的 `LDAP authentication raised` / `LDAP search found no match` 警告 |
| `AUTH_LDAP_ENABLED=true` 但 LDAP 没生效 | URL 或 BASE 为空时后端会自动降级为关闭，并打印 warning。检查 `.env` 是否被正确加载、变量名是否拼写正确 |
| 单个用户登录失败但其他人正常 | 该用户的 sAMAccountName 在目录中不存在、或多条匹配，或密码错误 |
| 邮箱显示成 `<username>@yumchina.com` 而非真实邮箱 | 该用户在 AD 里没有 `mail` 属性，或 `AUTH_LDAP_ATTR_EMAIL` 配置的属性名不对 |
| 登录报 `CSRF token missing` | 不应发生。若出现，检查 `/api/v1/auth/login` 是否在 `csrf_middleware.py` 的 `_AUTH_EXEMPT_PATHS` 中 |
| 想临时关掉 LDAP 验证 | 把 `AUTH_LDAP_ENABLED` 改成 `false` 并重启后端 |

---

## 8. 配置生效方式

**改完 `.env` 必须重启后端**才会生效。

LDAP 配置在首次访问 `get_auth_config()` 时加载并缓存，不支持热重载。

```bash
# 本地开发
make dev

# Docker
make up
```

---

## 9. 相关代码位置

| 文件 | 职责 |
|------|------|
| `backend/app/gateway/auth/ldap_config.py` | `LdapConfig` 模型 + 环境变量解析（含 DN 空格规范化） |
| `backend/app/gateway/auth/ldap_provider.py` | `LdapAuthProvider`：两阶段 bind、影子用户管理、过滤器转义 |
| `backend/app/gateway/auth/config.py` | `AuthConfig.ldap` 字段，启动时并入主配置 |
| `backend/app/gateway/deps.py` | `get_ldap_provider()` 单例（与本地 provider 共用 users 表） |
| `backend/app/gateway/routers/auth.py` | 登录路由 + `_resolve_login` 分派逻辑 |
| `backend/app/gateway/csrf_middleware.py` | CSRF 免检名单（含 `/api/v1/auth/login`） |
| `backend/app/gateway/auth_middleware.py` | Auth 公开路径名单（含 `/api/v1/auth/login`） |
| `backend/tests/test_ldap_auth.py` | 26 个单元测试（mock ldap3，覆盖分派/影子用户/失败/冲突） |

---

## 10. 测试验证

```bash
cd backend
# 运行全部 LDAP 相关测试（mock ldap3，不依赖真实 AD）
.venv/Scripts/python.exe -m pytest tests/test_ldap_auth.py -v
```

如需对真实 AD 做联通性验证，可临时用一个已知账号手动调用：

```bash
curl -X POST http://localhost:2026/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"你的sAMAccountName","password":"你的密码"}'
```

成功会返回 `{"expires_in": ..., "needs_setup": false}` 并设置 `access_token` Cookie。
