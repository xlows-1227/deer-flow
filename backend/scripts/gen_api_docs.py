#!/usr/bin/env python3
"""Generate per-module API reference docs (Chinese, field-level) from the
DeerFlow Gateway OpenAPI spec produced by app.gateway.app.create_app().

Run from backend/:
    uv run python /tmp/gen_api_docs.py /tmp/deerflow_openapi.json <repo_root>/docs/reference/api
"""

import json
import os
import sys
from collections import OrderedDict, defaultdict

SPEC_PATH = sys.argv[1]
OUT_DIR = sys.argv[2]

with open(SPEC_PATH, encoding="utf-8") as f:
    SPEC = json.load(f)

PATHS = SPEC.get("paths", {})
SCHEMAS = SPEC.get("components", {}).get("schemas", {})

METHOD_ORDER = ["get", "post", "put", "patch", "delete"]
METHOD_CN = {"get": "查询", "post": "创建", "put": "更新/覆盖", "patch": "部分更新", "delete": "删除"}


def ref_name(s):
    if isinstance(s, dict) and "$ref" in s:
        return s["$ref"].split("/")[-1]
    return None


def deref(s):
    """Follow a single top-level $ref to its schema dict; return as-is otherwise."""
    if isinstance(s, dict) and "$ref" in s:
        name = ref_name(s)
        return SCHEMAS.get(name, {})
    return s or {}


def merged_properties(schema):
    """Merge allOf inheritance -> (properties, required)."""
    props = OrderedDict()
    required = []

    def consume(s):
        if not isinstance(s, dict):
            return
        for sub in s.get("allOf", []):
            rn = ref_name(sub)
            if rn and rn in SCHEMAS:
                consume(SCHEMAS[rn])
            else:
                consume(sub)
        for k, v in (s.get("properties") or {}).items():
            props[k] = v
        for r in s.get("required", []):
            required.append(r)

    consume(deref(schema))
    return props, list(dict.fromkeys(required))


def type_str(s):
    if not s:
        return ""
    if isinstance(s, dict) and "$ref" in s:
        return ref_name(s)
    if "anyOf" in s:
        parts = [p for p in s["anyOf"] if not (isinstance(p, dict) and p.get("type") == "null")]
        if len(parts) == 1:
            return type_str(parts[0])
        return " | ".join(type_str(p) for p in parts)
    if "oneOf" in s:
        return " | ".join(type_str(p) for p in s["oneOf"])
    if "allOf" in s:
        names = [type_str(p) for p in s["allOf"] if ref_name(p)]
        return " & ".join(n for n in names if n) or "object"
    t = s.get("type")
    if t == "array":
        return f"array<{type_str(s.get('items'))}>"
    if t == "object":
        return "object"
    fmt = s.get("format")
    base = t or ""
    if fmt:
        base = f"{base}({fmt})" if base else fmt
    if "enum" in s and s["enum"] is not None:
        vals = ", ".join(str(v) for v in s["enum"])
        return f"{base or 'string'}（枚举: {vals}）"
    return base


def collect_refs(s, acc):
    """Recursively collect referenced schema names used anywhere in a schema."""
    if not isinstance(s, dict):
        return
    rn = ref_name(s)
    if rn:
        if rn in acc:
            return
        acc.add(rn)
        collect_refs(SCHEMAS.get(rn, {}), acc)
        return
    for key in ("items", "additionalProperties"):
        if key in s:
            collect_refs(s[key], acc)
    for sub in s.get("allOf", []) + s.get("anyOf", []) + s.get("oneOf", []):
        collect_refs(sub, acc)
    for prop in (s.get("properties") or {}).values():
        collect_refs(prop, acc)


def desc_of(s):
    if not isinstance(s, dict):
        return ""
    d = s.get("description")
    if d:
        return d.strip()
    # property without description: fall back to title
    return (s.get("title") or "").strip()


def render_field_table(schema):
    props, required = merged_properties(schema)
    if not props:
        return None
    lines = [
        "| 字段 | 类型 | 必填 | 说明 |",
        "| --- | --- | :---: | --- |",
    ]
    for name, p in props.items():
        req = "是" if name in required else "否"
        d = desc_of(p)
        lines.append(f"| `{name}` | {type_str(p)} | {req} | {d} |")
    return "\n".join(lines)


def render_model_section(name):
    schema = SCHEMAS.get(name)
    if not schema:
        return f"### {name}\n\n（未找到模型定义）\n"
    d = desc_of(schema)
    out = [f"### `{name}`", ""]
    if d:
        out += [d, ""]
    tbl = render_field_table(schema)
    if tbl:
        out += [tbl, ""]
    else:
        out += ["（无固定字段 / 任意结构）", ""]
    return "\n".join(out)


# ---- module metadata --------------------------------------------------------

MODULES = OrderedDict(
    [
        ("health", ("健康检查 Health", "/health", "public")),
        ("auth", ("认证 Auth", "/api/v1/auth", "mixed")),
        ("api_keys", ("API Key 管理 API Keys", "/api/v1/api-keys", "session")),
        ("external_api", ("外部 API V1 External API", "/api/v1/external", "apikey")),
        ("models", ("模型 Models", "/api/models", "session")),
        ("user_models", ("自定义模型 Custom Models", "/api/models/custom", "session")),
        ("mcp", ("MCP 配置 MCP", "/api/mcp", "session")),
        ("tools", ("工具配置 Tools", "/api/tools", "session")),
        ("memory", ("记忆 Memory", "/api/memory", "session")),
        ("skills", ("技能 Skills", "/api/skills", "session")),
        ("agents", ("智能体 Agents", "/api/agents", "session")),
        ("threads", ("会话 Threads", "/api/threads", "session")),
        ("thread_runs", ("会话内运行 Thread Runs", "/api/threads/{thread_id}/runs", "session")),
        ("runs", ("无状态运行 Stateless Runs", "/api/runs", "session")),
        ("feedback", ("反馈 Feedback", "/api/threads/{thread_id}/runs/{run_id}/feedback", "session")),
        ("uploads", ("上传 Uploads", "/api/threads/{thread_id}/uploads", "session")),
        ("files", ("文件库 Files", "/api/files", "session")),
        ("artifacts", ("生成物 Artifacts", "/api/threads/{thread_id}/artifacts", "session")),
        ("suggestions", ("建议问题 Suggestions", "/api/threads/{thread_id}/suggestions", "session")),
        ("shares", ("分享 Shares", "/api/threads · /api/share", "session")),
        ("scheduler", ("定时任务 Scheduler", "/api/scheduler", "session")),
        ("channels", ("IM 渠道 Channels", "/api/channels", "session")),
        ("connectors", ("连接器平台 Connectors", "/api/connectors · /api/connector-types", "session")),
        ("assistants_compat", ("Assistants 兼容 Assistants Compat", "/api/assistants", "session")),
    ]
)

MODULE_FILE = {
    "health": "health",
    "auth": "auth",
    "api_keys": "api-keys",
    "external_api": "external-api",
    "models": "models",
    "user_models": "user-models",
    "mcp": "mcp",
    "tools": "tools",
    "memory": "memory",
    "skills": "skills",
    "agents": "agents",
    "threads": "threads",
    "thread_runs": "thread-runs",
    "runs": "runs",
    "feedback": "feedback",
    "uploads": "uploads",
    "files": "files",
    "artifacts": "artifacts",
    "suggestions": "suggestions",
    "shares": "shares",
    "scheduler": "scheduler",
    "channels": "channels",
    "connectors": "connectors",
    "assistants_compat": "assistants-compat",
}

AUTH_NOTE = {
    "public": "公开接口，无需认证。",
    "session": "需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。",
    "apikey": "使用 Bearer API Key 认证：请求头 `Authorization: Bearer <API_KEY>`，仅能访问 `/api/v1/external/*`。",
    "mixed": "部分接口公开（登录/注册/初始化/OAuth 回调），部分需要已登录会话（详见各接口说明）。",
}


def module_of(op, path):
    tags = op.get("tags", [])
    t = tags[0] if tags else "misc"
    if t == "models":
        return "user_models" if path.startswith("/api/models/custom") else "models"
    if t == "runs":
        return "thread_runs" if path.startswith("/api/threads/") else "runs"
    if t == "external-api-keys":
        return "api_keys"
    if t == "external-api":
        return "external_api"
    if t == "assistants-compat":
        return "assistants_compat"
    return t


# ---- group operations by module --------------------------------------------

MOD_OPS = defaultdict(list)  # module -> list[(path, method, op)]
for path, item in PATHS.items():
    if not isinstance(item, dict):
        continue
    for method in METHOD_ORDER:
        if method in item and isinstance(item[method], dict):
            op = item[method]
            MOD_OPS[module_of(op, path)].append((path, method, op))


def first_line(text, n=120):
    if not text:
        return ""
    line = text.strip().split("\n", 1)[0].strip()
    if len(line) > n:
        line = line[: n - 1] + "…"
    return line


def endpoint_anchor(method, path):
    keep = path.strip("/").replace("/", "-").replace("{", "").replace("}", "").replace("_", "-")
    return f"{method}-{keep}" if keep else f"{method}-root"


def render_endpoint(path, method, op, model_refs):
    method_up = method.upper()
    summary = op.get("summary") or ""
    description = op.get("description") or ""
    anchor = endpoint_anchor(method, path)
    out = [f"## `{method_up} {path}`", ""]
    if summary:
        out.append(f"> {summary}  ")
    else:
        out.append(f"> {METHOD_CN.get(method, '')}操作  ")
    out.append(f'<a id="{anchor}"></a>')
    out.append("")
    if description:
        out += [description.strip(), ""]

    # parameters (path / query / header)
    params = [p for p in (op.get("parameters") or []) if isinstance(p, dict)]
    path_params = [p for p in params if p.get("in") == "path"]
    query_params = [p for p in params if p.get("in") == "query"]
    header_params = [p for p in params if p.get("in") == "header"]
    for title, plist in (("路径参数", path_params), ("查询参数", query_params), ("请求头", header_params)):
        if not plist:
            continue
        out += [f"**{title}**", "", "| 名称 | 类型 | 必填 | 说明 |", "| --- | --- | :---: | --- |"]
        for p in plist:
            req = "是" if p.get("required") else "否"
            sch = p.get("schema") or {}
            out.append(f"| `{p.get('name')}` | {type_str(sch)} | {req} | {(p.get('description') or '').strip()} |")
        out.append("")

    # request body
    rb = op.get("requestBody")
    if rb:
        content = rb.get("content") or {}
        if "application/json" in content:
            sch = content["application/json"].get("schema", {})
            out += ["**请求体**（`application/json`）", ""]
            collect_refs(sch, model_refs)
            tbl = render_field_table(sch)
            if tbl:
                out += [tbl, ""]
            else:
                out += [f"类型：`{type_str(sch)}`", ""]
        elif "multipart/form-data" in content:
            sch = content["multipart/form-data"].get("schema", {})
            out += ["**请求体**（`multipart/form-data`）", ""]
            collect_refs(sch, model_refs)
            props, req = merged_properties(sch)
            if props:
                out += ["| 字段 | 类型 | 必填 | 说明 |", "| --- | --- | :---: | --- |"]
                for name, p in props.items():
                    r = "是" if name in req else "否"
                    out.append(f"| `{name}` | {type_str(p) if ref_name(p) is None else 'file / ' + type_str(p)} | {r} | {desc_of(p)} |")
                out.append("")
        else:
            ctype = next(iter(content.keys()))
            out += [f"**请求体**（`{ctype}`）", ""]

    # responses
    responses = op.get("responses") or {}
    if responses:
        out += ["**响应**", ""]
        for code in sorted(responses.keys()):
            resp = responses[code]
            rdesc = (resp.get("description") or "").strip()
            out.append(f"- **`{code}`** {rdesc}")
            rcontent = resp.get("content") or {}
            for ctype, cobj in rcontent.items():
                rsch = cobj.get("schema", {})
                if rsch:
                    collect_refs(rsch, model_refs)
                    out.append(f"  - 响应体（`{ctype}`）：`{type_str(rsch)}`，字段见 [数据模型](#数据模型)。")
        out.append("")

    # curl example
    base = "${DEERFLOW_BASE_URL}"
    curl = [f"curl -X {method_up} '{base}{path}'"]
    if rb and "application/json" in (rb.get("content") or {}):
        curl.append("  -H 'Content-Type: application/json'")
        curl.append("  -d '{ … }'")
    out += ["**请求示例（cURL）**", "", "```bash", "\n".join(curl), "```", ""]
    return "\n".join(out)


def render_module(module_id):
    title, base_path, auth_kind = MODULES[module_id]
    ops = sorted(MOD_OPS.get(module_id, []), key=lambda x: (x[0], METHOD_ORDER.index(x[1])))
    fname = MODULE_FILE[module_id]
    out = [f"# {title}", ""]
    out += [f"> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`{base_path}`", f"> 认证：{AUTH_NOTE[auth_kind]}", ""]
    out += ["## 接口清单", "", "| 方法 | 路径 | 用途 |", "| --- | --- | --- |"]
    for path, method, op in ops:
        out.append(f"| [`{method.upper()}`](#{endpoint_anchor(method, path)}) | `{path}` | {first_line(op.get('summary') or op.get('description'))} |")
    out.append("")

    model_refs = set()
    for path, method, op in ops:
        out.append(render_endpoint(path, method, op, model_refs))
        out.append("---")
        out.append("")

    # filter refs to those that have a schema definition
    model_refs = sorted(r for r in model_refs if r in SCHEMAS)
    if model_refs:
        out += ["## 数据模型", ""]
        for name in model_refs:
            out.append(render_model_section(name))
    return "\n".join(out), fname, len(ops)


os.makedirs(OUT_DIR, exist_ok=True)
counts = {}
for mid in MODULES:
    if mid not in MOD_OPS:
        continue
    content, fname, n = render_module(mid)
    with open(os.path.join(OUT_DIR, fname + ".md"), "w", encoding="utf-8") as f:
        f.write(content.rstrip() + "\n")
    counts[fname] = n
    print(f"wrote {fname}.md  ({n} endpoints)")

# ---- index -----------------------------------------------------------------
idx = [
    "# DeerFlow Gateway 接口文档（按模块）",
    "",
    "> 本目录按后端 `app/gateway/routers/` 路由模块组织，自动生成自 FastAPI OpenAPI 规范（`create_app().openapi()`）。",
    "> 如需修改接口，请调整对应路由与 Pydantic 模型后重新生成；不要直接手改本目录的接口清单与字段表。",
    "",
    "## 通用约定",
    "",
    "- **统一入口**：生产/开发均通过 Nginx（默认端口 `2026`）访问。`/api/langgraph/*` 转发到内嵌 LangGraph 运行时，其余 `/api/*` 转发到 Gateway REST API。",
    "- **基础地址**：`http(s)://<host>:2026`，下文示例以 `${DEERFLOW_BASE_URL}` 表示。",
    "- **认证**：除 `健康检查`、`认证`、`外部 API V1` 外，绝大多数接口要求已登录的浏览器会话（Cookie）；"
    "写操作还需 CSRF 令牌（Double Submit Cookie，由前端自动携带）。"
    "`外部 API V1` 使用 `Authorization: Bearer <API_KEY>` 的用户级 API Key，且仅可访问 `/api/v1/external/*`。",
    "- **请求/响应格式**：有请求体的接口使用 `application/json`（文件上传使用 `multipart/form-data`）；响应体默认 `application/json`。",
    '- **错误处理**：`/api/v1/external/*` 返回统一错误信封 `{"error": {code, message, request_id, details}}`；其余接口遵循 FastAPI 默认错误格式（`detail` 字段）。',
    "- **请求 ID**：响应头 `X-Request-ID`，便于问题定位。",
    "- **交互式文档**：开发环境可访问 `/docs`（Swagger）与 `/redoc`；设置环境变量 `GATEWAY_ENABLE_DOCS=false` 可在生产关闭。",
    "",
    "## 模块索引",
    "",
    "| 模块 | 文档 | 基础路径 | 端点数 | 认证 |",
    "| --- | --- | --- | :---: | --- |",
]
for mid, (title, base_path, auth_kind) in MODULES.items():
    fname = MODULE_FILE[mid]
    n = counts.get(fname, 0)
    idx.append(f"| {title} | [{fname}.md](./{fname}.md) | `{base_path}` | {n} | {AUTH_NOTE[auth_kind]} |")
idx += [
    "",
    "## 相关文档",
    "",
    "- 外部 API 接入手册（业务方接入用）：[../EXTERNAL_API_V1_zh.md](../EXTERNAL_API_V1_zh.md)",
    "- 外部 API 机器可读规范：[../external-api-v1.openapi.yaml](../external-api-v1.openapi.yaml)",
    "- 外部 API 测试手册：[../EXTERNAL_API_V1_TEST_MANUAL_zh.md](../EXTERNAL_API_V1_TEST_MANUAL_zh.md)",
    "",
]
with open(os.path.join(OUT_DIR, "README.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(idx))
print("wrote README.md (index)")
print("TOTAL endpoints:", sum(counts.values()))
