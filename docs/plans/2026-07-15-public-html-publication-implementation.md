# HTML 对外发布功能实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**目标：** 为“对话生成”中的 HTML 文件增加永久、可撤销、无需登录且支持交互的公开访问链接。

**架构：** 新增独立的 `file_publications` 表和路由，以用户、对话和生成文件路径为唯一来源，公开 token 只暴露安全元数据和纯文本 HTML。前端文件页加载当前用户的发布状态，公开页再把 HTML 文本注入不含 `allow-same-origin` 的交互式沙箱 iframe。

**技术栈：** FastAPI、SQLAlchemy 2、Alembic、SQLite/PostgreSQL、Next.js App Router、React、TanStack Query、Vitest、Playwright。

---

### 任务 1：持久化模型与迁移

**文件：**

- 新建：`backend/packages/harness/deerflow/persistence/file_publication/__init__.py`
- 新建：`backend/packages/harness/deerflow/persistence/file_publication/model.py`
- 新建：`backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_15_file_publications.py`
- 修改：`backend/packages/harness/deerflow/persistence/models/__init__.py`
- 新建测试：`backend/tests/test_file_publications_migration.py`

**步骤 1：先写失败的迁移测试**

从 Alembic 版本 `2026_07_13_file_shares` 初始化 SQLite，运行 `_run_pending_alembic_revisions()`，通过 `PRAGMA table_info(file_publications)` 断言以下字段：

```python
assert columns == {
    "id",
    "public_token",
    "owner_user_id",
    "thread_id",
    "source_path",
    "source_identity",
    "created_at",
}
assert version == "2026_07_15_file_publications"
```

**步骤 2：运行测试并确认红灯**

```powershell
uv run pytest tests/test_file_publications_migration.py -q
```

预期：失败，因为新迁移和表尚不存在。

**步骤 3：实现最小模型和迁移**

模型使用 UUID 字符串主键、唯一 `public_token`、级联删除的 `owner_user_id`，并添加：

```python
UniqueConstraint(
    "owner_user_id",
    "thread_id",
    "source_path",
    name="uq_file_publications_owner_source",
)
```

迁移的 `down_revision` 必须是 `2026_07_13_file_shares`，并创建所有者索引和 token 唯一索引。

**步骤 4：运行测试并确认绿灯**

运行同一条 pytest 命令，预期 1 项通过。

**步骤 5：提交**

```powershell
git add backend/packages/harness/deerflow/persistence backend/tests/test_file_publications_migration.py
git commit -m "feat(files): add html publication persistence"
```

### 任务 2：所有者发布与列表 API

**文件：**

- 新建：`backend/app/gateway/routers/file_publications.py`
- 修改：`backend/app/gateway/routers/__init__.py`
- 修改：`backend/app/gateway/app.py`
- 新建测试：`backend/tests/test_file_publications_router.py`

**步骤 1：写第一个路由失败测试**

通过真实 FastAPI 路由和测试 SQLite 数据库调用：

```python
response = await client.post(
    "/api/file-publications",
    json={
        "thread_id": "thread-1",
        "path": "/mnt/user-data/outputs/report.html",
    },
)
assert response.status_code == 201
assert response.json()["public_url"].startswith("/published/")

repeat = await client.post("/api/file-publications", json=response_request)
assert repeat.json()["id"] == response.json()["id"]
assert repeat.json()["public_token"] == response.json()["public_token"]
```

同一测试再通过 `GET /api/file-publications` 验证只列出当前用户记录。

**步骤 2：确认红灯**

```powershell
uv run pytest tests/test_file_publications_router.py -q
```

预期：404，因为路由尚未实现。

**步骤 3：实现最小发布接口**

实现请求和响应模型，以及以下约束：

- 必须有登录用户。
- `thread_id` 必须通过 `thread_store.check_access(..., require_existing=True)`。
- 路径必须位于 `/mnt/user-data/outputs/`。
- 后缀只能是 `.html` 或 `.htm`，忽略大小写。
- 通过 `Paths.resolve_virtual_path()` 定位文件并保存设备号/inode 身份。
- 重复发布原文件返回同一记录；明确重新发布替换文件时更新 `source_identity` 并保留 token。
- token 使用 `secrets.token_urlsafe(32)`，日志中不输出 token。

**步骤 4：补充并运行行为测试**

增加非 HTML、错误目录、文件不存在、跨用户对话四个公开行为断言。运行定向测试，预期全部通过。

**步骤 5：提交**

```powershell
git add backend/app/gateway/routers backend/app/gateway/app.py backend/tests/test_file_publications_router.py
git commit -m "feat(files): add html publication owner api"
```

### 任务 3：公开读取、撤销和文件替换保护

**文件：**

- 修改：`backend/app/gateway/routers/file_publications.py`
- 修改：`backend/app/gateway/auth_middleware.py`
- 修改测试：`backend/tests/test_file_publications_router.py`
- 修改测试：`backend/tests/test_auth_middleware.py`

**步骤 1：写公开读取失败测试**

发布 HTML 后，不携带登录信息调用：

```python
metadata = await public_client.get(f"/api/public-files/{token}")
assert metadata.json() == {
    "name": "report.html",
    "content_url": f"/api/public-files/{token}/content",
}

content = await public_client.get(metadata.json()["content_url"])
assert content.text == "<button onclick=\"this.textContent='done'\">run</button>"
assert content.headers["content-type"].startswith("text/plain")
assert content.headers["x-content-type-options"] == "nosniff"
```

再断言取消发布后、删除源文件后、同路径替换文件后均返回 404。

**步骤 2：确认红灯**

运行路由测试和 `test_auth_middleware.py`，预期公开前缀和接口均失败。

**步骤 3：实现最小公开接口与撤销接口**

- `GET /api/public-files/{token}` 只返回文件名和内容地址。
- `GET /api/public-files/{token}/content` 返回 `FileResponse`，媒体类型固定为 `text/plain; charset=utf-8`，并设置 `nosniff` 和 `no-store`。
- `DELETE /api/file-publications/{id}` 必须同时匹配所有者 ID。
- `_resolve_publication_source()` 必须重新验证文件存在及 `source_identity`。
- 将 `/api/public-files/` 加入 AuthMiddleware 的公开路径前缀。

**步骤 4：运行定向测试并确认绿灯**

```powershell
uv run pytest tests/test_file_publications_router.py tests/test_auth_middleware.py -q
```

**步骤 5：提交**

```powershell
git add backend/app/gateway/routers/file_publications.py backend/app/gateway/auth_middleware.py backend/tests
git commit -m "feat(files): serve and revoke public html links"
```

### 任务 4：前端发布 API 和查询状态

**文件：**

- 新建：`frontend/src/core/files/publication.ts`
- 修改：`frontend/src/core/files/hooks.ts`
- 修改：`frontend/src/core/files/index.ts`
- 新建测试：`frontend/tests/unit/core/files/file-publication-api.test.ts`

**步骤 1：写 API 失败测试**

在认证 fetch 边界断言：

```typescript
await publishGeneratedHtml(item);
expect(fetchWithAuth).toHaveBeenCalledWith(
  "http://localhost:8001/api/file-publications",
  expect.objectContaining({
    method: "POST",
    body: JSON.stringify({
      thread_id: "thread-1",
      path: "/mnt/user-data/outputs/report.html",
    }),
  }),
);
```

同时覆盖列表、取消发布、非生成 HTML 在客户端直接拒绝，以及无需认证 fetch 的公开元数据和内容读取。

**步骤 2：运行测试并确认红灯**

```powershell
pnpm test -- tests/unit/core/files/file-publication-api.test.ts
```

**步骤 3：实现最小 API 和 Hook**

导出：

- `FilePublicationRecord`
- `isPublishableGeneratedHtml(item)`
- `listFilePublications()`
- `publishGeneratedHtml(item)`
- `cancelFilePublication(id)`
- `loadPublishedHtml(token)`
- `useFilePublications({ enabled })`

Query key 固定为 `['files', 'publications']`，发布和取消成功后统一使其失效。

**步骤 4：运行测试并确认绿灯**

运行新测试和现有 `file-sharing-api.test.ts`，确保共享功能没有回归。

**步骤 5：提交**

```powershell
git add frontend/src/core/files frontend/tests/unit/core/files
git commit -m "feat(files): add html publication client"
```

### 任务 5：文件列表发布、复制和取消操作

**文件：**

- 修改：`frontend/src/app/workspace/files/page.tsx`
- 新建测试：`frontend/tests/e2e/file-publication.spec.ts`
- 可能修改：`frontend/tests/e2e/utils/mock-api.ts`

**步骤 1：写文件菜单 E2E 红灯测试**

模拟一个生成 HTML 和一个生成 PDF，进入“对话生成”后验证：

- HTML 菜单存在“发布外链”。
- PDF 菜单不存在发布操作。
- 发布后显示“复制外链”和“取消发布”。
- 复制地址以 `/published/{token}` 结尾。
- 取消确认后重新显示“发布外链”。

**步骤 2：运行单个 E2E 并确认红灯**

```powershell
pnpm test:e2e -- file-publication.spec.ts
```

**步骤 3：实现最小页面交互**

- 仅在 `systemFolder === 'generated'` 时启用发布查询。
- 使用 `${thread_id}:${path}` 建立记录映射。
- 未发布 HTML 添加带地球图标的“发布外链”。
- 已发布 HTML 添加“复制外链”和红色“取消发布”。
- 取消发布前使用 `window.confirm()`。
- 发布、复制、取消沿用现有 Toast 错误处理。

**步骤 4：运行 E2E 并确认绿灯**

重复单文件 E2E，预期通过。

**步骤 5：提交**

```powershell
git add frontend/src/app/workspace/files/page.tsx frontend/tests/e2e
git commit -m "feat(files): add html publication actions"
```

### 任务 6：无需登录的交互式公开页面

**文件：**

- 新建：`frontend/src/app/published/[token]/layout.tsx`
- 新建：`frontend/src/app/published/[token]/page.tsx`
- 新建测试：`frontend/tests/e2e/published-html.spec.ts`

**步骤 1：写公开页面 E2E 红灯测试**

模拟 HTML：

```html
<button id="run" onclick="this.textContent='已运行'">运行</button>
```

直接访问 `/published/public-token`，进入 iframe 点击按钮并断言文本变为“已运行”；同时断言 iframe sandbox 包含 `allow-scripts`、`allow-forms`，不包含 `allow-same-origin`。再覆盖 404 时的统一失效页面。

**步骤 2：运行测试并确认红灯**

```powershell
pnpm test:e2e -- published-html.spec.ts
```

**步骤 3：实现公开页面**

- `layout.tsx` 导出 `robots: { index: false, follow: false }` 元数据。
- 客户端页面从参数取得 token，通过 `loadPublishedHtml()` 加载内容。
- 成功时使用全屏 iframe、`sandbox="allow-scripts allow-forms"`、`referrerPolicy="no-referrer"` 和 `srcDoc`。
- 加载和错误状态使用无工作区导航的独立页面。

**步骤 4：运行 E2E 并确认绿灯**

重复单文件 E2E，预期交互和错误状态均通过。

**步骤 5：提交**

```powershell
git add frontend/src/app/published frontend/tests/e2e/published-html.spec.ts
git commit -m "feat(files): add public interactive html viewer"
```

### 任务 7：文档同步和完整验证

**文件：**

- 修改：`README.md`
- 修改：`backend/CLAUDE.md`
- 修改：`frontend/CLAUDE.md`
- 修改：`docs/plans/2026-07-15-public-html-publication-design.md`（仅在实现与设计产生差异时）

**步骤 1：同步文档**

记录新表、管理 API、公开 API、永久/撤销语义、纯文本传输和非同源 iframe 安全边界。

**步骤 2：运行后端验证**

```powershell
uv run pytest tests/test_file_publications_migration.py tests/test_file_publications_router.py tests/test_auth_middleware.py -q
uv run ruff check app/gateway/routers/file_publications.py packages/harness/deerflow/persistence/file_publication tests/test_file_publications_router.py tests/test_file_publications_migration.py
uv run python -m compileall app packages/harness/deerflow
```

**步骤 3：运行前端验证**

```powershell
pnpm test
pnpm typecheck
pnpm exec eslint src/core/files/publication.ts src/core/files/hooks.ts src/app/workspace/files/page.tsx src/app/published tests/unit/core/files tests/e2e/file-publication.spec.ts tests/e2e/published-html.spec.ts
pnpm test:e2e -- file-publication.spec.ts published-html.spec.ts
```

**步骤 4：浏览器视觉检查**

检查桌面尺寸下文件菜单的三种状态和公开页的加载、失效、成功状态，并实际点击公开 HTML 内按钮确认交互。

**步骤 5：检查差异并提交**

```powershell
git diff --check
git status --short
git add README.md backend/CLAUDE.md frontend/CLAUDE.md docs/plans
git commit -m "docs(files): document public html publication"
```
