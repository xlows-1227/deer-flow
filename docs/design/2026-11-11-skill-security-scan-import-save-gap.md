# 技能导入 / 编辑保存的安全拦截清单与边界修订

日期：2026-11-11（v2 修订）
适用版本：当前 `main` 工作区
范围：`POST /api/skills/upload`（导入）、`PUT /api/skills/custom/{name}` 及其同族写入路径（编辑保存）
状态：**本轮只读，未改任何生产代码与测试；本次不实施，待后续排期**

> 本文替代同路径 v1 版本。v1 中"`scripts/**` 一律硬拦、绝不 force"的结论**已被推翻**（见 §4.1），
> 保留本文是为了避免两份方案并存。v1 的其他取证（代码位置、错误结构、force 参数）仍然有效，已并入本文。

---

## 0. 结论先行（TL;DR）

1. 导入与编辑**共用同一个检测函数** `scan_skill_content`，规则文本、模型、`executable` 口径完全一致。差异只有两处：
   - **导入**把 `block` 包装成结构化错误并带 `can_force=true`，前端有「仍然上传」按钮，`force=true` 时**跳过全部扫描**；
   - **编辑保存**把同一个 `block` 直接变成 `400 + 纯字符串`，**没有 `force` 参数、没有 `can_force` 字段**，用户无路可走。
2. 用户真正的死锁场景不是"改 SKILL.md 被拦"，而是**改 `scripts/config.py` 这类带 key 的脚本被拦**：导入时勾了 force 能进来，进来之后一保存就永久 400。
3. **不能用"是否可执行"划边界**。业务技能的常态就是 `SKILL.md` 做入口 + `scripts/*.py` 调外部系统、脚本里放 key。正确维度是"**判的是什么**"：硬编码凭据 / 读本地凭据外发 / 远程代码执行 / 纯语法错误，四者的处置完全不同。
4. 实测证明 scanner **能**在语义上区分"读本地凭据外发"与"下载执行"（都 `block` 且 reason 明确），但对"**硬编码 key 正常调 API**"与"**读环境变量外发**"**不能稳定区分**：前者可能 `block`、后者可能 `warn`，同一段内容重复调用结果也可能翻转（LLM 判定，无缓存）。因此**任何"按 reason 文本自动放行"的规则都不可靠**，只能作为 `can_force` 的展示理由，不能作为自动决策。
5. 最小切法：只给**编辑保存侧**加"结构化错误 + `can_force` + `force` 出口"，**不碰导入侧、不改 scanner、不统一扫描范围**。改动约 2 个前端文件 + 1 个后端文件，量级 70~110 行，可一版收。

---

## 1. 问题陈述

同一份技能内容：

- 通过「上传技能」导入时，若命中敏感内容，页面提示「上传失败 / 原因：…」并给出**「仍然上传」**按钮；用户确认后安装成功。
- 装好之后在编辑器里改这个技能并保存，报同一个安全原因，但只有 `toast.error`，**没有"仍然保存"**，且后端接口不接受任何放行参数。

结果：用户被永久卡死——不能通过 GUI 修改一个自己明确知道有 key 的业务脚本。

---

## 2. 现状证据

### 2.1 两条链路

| 侧 | 调用链 |
| --- | --- |
| 导入 | `frontend/src/app/workspace/skills/upload/page.tsx` → `core/skills/api.ts` `uploadSkillArchive` → `POST /api/skills/upload?force=` → `LocalSkillStorage.ainstall_skill_from_archive` → `_scan_skill_archive_contents_or_raise` → `_scan_skill_file_or_raise` → `scan_skill_content` |
| 编辑保存 | `skill-editor-workspace.tsx` `applyChanges()` → `updateCustomSkill()` → `PUT /api/skills/custom/{name}` → 路由内联 `scan_skill_content` |

### 2.2 同一个检测函数

[security_scanner.py#L70-L113](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/security_scanner.py#L70-L113)

- rubric 文本 L72-L79：`allow | warn | block`，`block` 覆盖 prompt-injection、system-role override、privilege escalation、**exfiltration**、**unsafe executable code**；`warn` 用于 borderline external API references。
- 模型 L85-L86：`skill_evolution.moderation_model_name`，为空时用默认模型。
- 失败兜底 L105-L113：**有响应但解析失败 → `block`**；**模型不可用 + 可执行 → `block`**；**模型不可用 + 非可执行 → `warn`（放行）**。

### 2.3 force 参数位置与语义

| 位置 | 内容 |
| --- | --- |
| [skills.py#L836](file:///c:/projects/deer-flow/backend/app/gateway/routers/skills.py#L836) | `force: bool = Query(False, ...)` |
| [skills.py#L847](file:///c:/projects/deer-flow/backend/app/gateway/routers/skills.py#L847) | `ainstall_skill_from_archive(tmp_path, skip_security_scan=force)` |
| [installer.py#L230](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/installer.py#L230) | `skip_security_scan` 为真 → 跳过 SKILL.md 扫描 |
| [installer.py#L242-L243](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/installer.py#L242-L243) | 跳过所有支撑文件扫描 |

**即 `force=true` 是"整段扫描直接不做"，不是"放行某一条"。** 这是后文分级讨论的关键事实。

### 2.4 保存侧没有出口

| 位置 | 内容 |
| --- | --- |
| [skills.py#L1476-L1477](file:///c:/projects/deer-flow/backend/app/gateway/routers/skills.py#L1476-L1477) | `update_custom_skill` 签名**无** `force` |
| [skills.py#L1484-L1485](file:///c:/projects/deer-flow/backend/app/gateway/routers/skills.py#L1484-L1485) | `block` → `HTTPException(400, detail=f"Security scan blocked the edit: {scan.reason}")` |
| [skills.py#L1284-L1287](file:///c:/projects/deer-flow/backend/app/gateway/routers/skills.py#L1284-L1287) | 写支撑文件：`block` → 400；`executable && decision != allow` → 400 |

### 2.5 前端把结构化信息丢了（这是修复的真正堵点）

- 导入：`normalizeErrorDetail` 会解析 `{code, message, reason, can_force}`（[api.ts#L21-L46](file:///c:/projects/deer-flow/frontend/src/core/skills/api.ts#L21-L46)）。
- 保存：`readErrorMessage` 只返回 `message`（[api.ts#L48-L51](file:///c:/projects/deer-flow/frontend/src/core/skills/api.ts#L48-L51)），`updateCustomSkill`/`writeCustomSkillFile` 都走它（[api.ts#L383-L403](file:///c:/projects/deer-flow/frontend/src/core/skills/api.ts#L383-L403)、[api.ts#L297-L318](file:///c:/projects/deer-flow/frontend/src/core/skills/api.ts#L297-L318)）。
- **所以即使后端改成返回 `can_force`，按当前前端代码也传不到编辑器**，必须同时改 `api.ts`。

---

## 3. 完整拦截清单（以代码为准穷举）

说明：
- "前端文案"指用户实际看到的那句。结构化的走 `message`（并为 `reason` 单列一行「原因：」）；非结构化 400/413 的 `detail` 是字符串，`normalizeErrorDetail` 会原样作为 `message`。
- 导入侧只要 `detail` 是 `SkillUploadErrorDetail`，前端**一定**会渲染「原因：」；但**只有 `can_force === true`** 才渲染按钮。
- `can_force` 缺省值为 `False`（`SkillSecurityScanError.__init__` [installer.py#L35-L38](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/installer.py#L35-L38)）。

### 3.1 导入侧（`POST /api/skills/upload`）

| # | 触发条件（代码位置） | `code` / 前端文案 | `can_force` | 点 force 后能否过 |
| --- | --- | --- | --- | --- |
| I1 | 扩展名非 `.zip/.skill`（[skills.py#L841-L842](file:///c:/projects/deer-flow/backend/app/gateway/routers/skills.py#L841-L842)；存储层同判 [local_skill_storage.py#L261-L262](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/storage/local_skill_storage.py#L261-L262)） | 无 code（纯字符串）`File must have .skill or .zip extension` | 无（非结构化） | **不能**，force 不改变后缀校验 |
| I2 | 上传字节 > 100 MiB（[skills.py#L33-L34](file:///c:/projects/deer-flow/backend/app/gateway/routers/skills.py#L33-L34)、[L48-L52](file:///c:/projects/deer-flow/backend/app/gateway/routers/skills.py#L48-L52)） | **413** 纯字符串 `Skill archive too large: maximum is 100.0 MB` | 无 | **不能** |
| I3 | 不是合法 ZIP（[local_skill_storage.py#L274-L275](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/storage/local_skill_storage.py#L274-L275) → [skills.py#L882-L889](file:///c:/projects/deer-flow/backend/app/gateway/routers/skills.py#L882-L889)） | `invalid_archive`；`File is not a valid ZIP archive` + 原因`The uploaded file is not a valid skill archive.` | `False` | **不能** |
| I4 | 归档成员为绝对路径或含 `..`（[installer.py#L150-L151](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/installer.py#L150-L151)） | `invalid_archive`；`Archive contains unsafe member path: '...'` | `False` | **不能**（在解压阶段，早于 force） |
| I5 | 解压后条目逃逸目标目录（[installer.py#L159-L160](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/installer.py#L159-L160)） | `invalid_archive`；`Zip entry escapes destination: '...'` | `False` | **不能** |
| I6 | 解压总量 > 512 MiB（zip bomb 防护，[installer.py#L170-L171](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/installer.py#L170-L171)） | `invalid_archive`；`Skill archive is too large or appears highly compressed.` | `False` | **不能** |
| I7 | 过滤后归档为空（[installer.py#L124-L125](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/installer.py#L124-L125)） | `invalid_archive`；`Skill archive is empty` | `False` | **不能** |
| I8 | 缺 `SKILL.md`（[validation.py#L40-L41](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/validation.py#L40-L41) → [local_skill_storage.py#L282-L284](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/storage/local_skill_storage.py#L282-L284)） | `invalid_archive`；`Invalid skill: SKILL.md not found` | `False` | **不能**（校验在扫描前） |
| I9 | 文件不以 `---` 开头（[validation.py#L44-L45](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/validation.py#L44-L45)） | `invalid_archive`；`Invalid skill: No YAML frontmatter found` | `False` | **不能** |
| I10 | frontmatter 不匹配 `^---\n...\n---`（[validation.py#L48-L50](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/validation.py#L48-L50)） | `invalid_archive`；`Invalid skill: Invalid frontmatter format` | `False` | **不能** |
| I11 | YAML 解析失败（[validation.py#L59-L60](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/validation.py#L59-L60)） | `invalid_archive`；`Invalid skill: Invalid YAML in frontmatter: ...` | `False` | **不能** |
| I12 | frontmatter 不是字典（[validation.py#L56-L58](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/validation.py#L56-L58)） | `invalid_archive`；`Invalid skill: Frontmatter must be a YAML dictionary` | `False` | **不能** |
| I13 | 出现白名单外的 key（[validation.py#L63-L65](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/validation.py#L63-L65)） | `invalid_archive`；`Invalid skill: Unexpected key(s) in SKILL.md frontmatter: ...` | `False` | **不能** |
| I14 | 缺 `name`（[validation.py#L68-L69](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/validation.py#L68-L69)） | `invalid_archive`；`Invalid skill: Missing 'name' in frontmatter` | `False` | **不能** |
| I15 | 缺 `description`（[validation.py#L70-L71](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/validation.py#L70-L71)） | `invalid_archive`；`Invalid skill: Missing 'description' in frontmatter` | `False` | **不能** |
| I16 | `name` 非字符串（[validation.py#L75-L76](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/validation.py#L75-L76)） | `invalid_archive`；`Invalid skill: Name must be a string, got ...` | `False` | **不能** |
| I17 | `name` 为空（[validation.py#L78-L79](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/validation.py#L78-L79)） | `invalid_archive`；`Invalid skill: Name cannot be empty` | `False` | **不能** |
| I18 | `name` 非 hyphen-case（[validation.py#L82-L83](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/validation.py#L82-L83)） | `invalid_archive`；`Invalid skill: Name '...' should be hyphen-case ...` | `False` | **不能** |
| I19 | `name` 首/尾连字符或连续连字符（[validation.py#L84-L85](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/validation.py#L84-L85)） | `invalid_archive`；`Invalid skill: Name '...' cannot start/end with hyphen ...` | `False` | **不能** |
| I20 | `name` > 64 字符（[validation.py#L86-L87](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/validation.py#L86-L87)） | `invalid_archive`；`Invalid skill: Name is too long ...` | `False` | **不能** |
| I21 | `description` 非字符串（[validation.py#L91-L92](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/validation.py#L91-L92)） | `invalid_archive`；`Invalid skill: Description must be a string ...` | `False` | **不能** |
| I22 | `description` 含 `<` / `>`（[validation.py#L95-L96](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/validation.py#L95-L96)） | `invalid_archive`；`Invalid skill: Description cannot contain angle brackets ...` | `False` | **不能** |
| I23 | `description` > 1024 字符（[validation.py#L97-L98](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/validation.py#L97-L98)） | `invalid_archive`；`Invalid skill: Description is too long ...` | `False` | **不能** |
| I24 | `allowed-tools` / `requires` 解析失败（[validation.py#L100-L104](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/validation.py#L100-L104)） | `invalid_archive`；`Invalid skill: <解析器原文>` | `False` | **不能** |
| I25 | 解析出的 name 含 `/`、`\`、`..`（[local_skill_storage.py#L285-L286](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/storage/local_skill_storage.py#L285-L286)） | `invalid_archive`；`Invalid skill name: ...` | `False` | **不能** |
| I26 | 同名技能已存在（[local_skill_storage.py#L288-L290](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/storage/local_skill_storage.py#L288-L290) → [skills.py#L864-L871](file:///c:/projects/deer-flow/backend/app/gateway/routers/skills.py#L864-L871)） | **409** `skill_already_exists`；原因`A skill with this name already exists. ...` | `False` | **不能** |
| I27 | SKILL.md 非 UTF-8（[installer.py#L206-L208](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/installer.py#L206-L208)） | `security_scan_failed`；`Security scan failed for skill 'x': x/SKILL.md must be valid UTF-8` | **`True`** | **能**（force 跳过读取与扫描） |
| I28 | 支撑脚本非 UTF-8（同一处，location 为脚本路径） | `security_scan_failed`；`... x/scripts/y.py must be valid UTF-8` | **`True`** | **能** |
| I29 | 扫描过程抛异常（[installer.py#L212-L213](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/installer.py#L212-L213)） | `security_scan_failed`；`Security scan failed for {location}: {e}` | **`True`** | **能** |
| I30 | SKILL.md 判 `block`（[installer.py#L217-L219](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/installer.py#L217-L219)） | `security_scan_failed`；`Security scan blocked skill 'x': {reason}` | **`True`** | **能** |
| I31 | 支撑文件判 `block`（[installer.py#L220](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/installer.py#L220)） | `security_scan_failed`；`Security scan blocked {location}: {reason}` | **`True`** | **能** |
| I32 | `scripts/**` 判 `warn` 或任何非 `allow`（[installer.py#L221-L222](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/installer.py#L221-L222)） | `security_scan_failed`；`Security scan rejected executable {location}: {reason}` | **`True`** | **能** |
| I33 | scanner 返回非法 decision（[installer.py#L223-L224](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/installer.py#L223-L224)） | `security_scan_failed`；`... invalid scanner decision 'x'` | **`True`** | **能** |
| I34 | 归档内出现第二个 `SKILL.md`（[installer.py#L240-L241](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/installer.py#L240-L241)） | `security_scan_failed`；`... nested SKILL.md is not allowed at ...` | **`False`**（构造未传 can_force） | **不能**，该检查在 `skip_security_scan` 之前 |
| I35 | 其他未预期异常（[skills.py#L890-L898](file:///c:/projects/deer-flow/backend/app/gateway/routers/skills.py#L890-L898)） | **500** `upload_failed` | `False` | **不能** |

前端额外一道（不产生后端 code）：选择文件时若非 `.zip/.skill`，只 `toast.error("请上传 .zip 或 .skill 文件")`，同时上传按钮置灰（[upload/page.tsx#L40](file:///c:/projects/deer-flow/frontend/src/app/workspace/skills/upload/page.tsx#L40)、[L96-L98](file:///c:/projects/deer-flow/frontend/src/app/workspace/skills/upload/page.tsx#L96-L98)）。

### 3.2 编辑保存侧（同一份内容）

| # | 触发条件（代码位置） | 前端实际文案 | 有无 `can_force` | 保存侧能否越过 |
| --- | --- | --- | --- | --- |
| S1 | 技能不存在 / 无权限（[local_skill_storage.py#L198-L215](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/storage/local_skill_storage.py#L198-L215)） | **404** `Custom skill 'x' not found.` | 无 | **不能**（属存在性问题，本就不该有出口） |
| S2 | frontmatter 任一问题（含 I8~I24，[skill_storage.py#L63-L75](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/storage/skill_storage.py#L63-L75)） | **400** 纯字符串，内容同上表 `Invalid skill` 之后的原文 | 无 | **不能** |
| S3 | frontmatter `name` 与 URL 中的技能名不一致（[skill_storage.py#L76-L77](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/storage/skill_storage.py#L76-L77)） | **400** `Frontmatter name 'a' must match requested skill name 'b'.` | 无 | **不能** |
| S4 | **SKILL.md 判 `block`**（[skills.py#L1483-L1485](file:///c:/projects/deer-flow/backend/app/gateway/routers/skills.py#L1483-L1485)） | **400** `Security scan blocked the edit: {reason}` | 无 | **不能**（无 force 参数） |
| S5 | 写支撑文件：路径为空 / 绝对路径 / 含 `..` / 不在五个白名单目录下（[skill_storage.py#L79-L108](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/skills/storage/skill_storage.py#L79-L108)） | **400** 纯字符串 `Path must be relative.` 等 | 无 | **不能** |
| S6 | **支撑文件判 `block`**（[skills.py#L1284-L1285](file:///c:/projects/deer-flow/backend/app/gateway/routers/skills.py#L1284-L1285)） | **400** `Security scan blocked the write: {reason}` | 无 | **不能** |
| S7 | **`scripts/**` 判 `warn` 或任何非 `allow`**（[skills.py#L1286-L1287](file:///c:/projects/deer-flow/backend/app/gateway/routers/skills.py#L1286-L1287)） | **400** `Security scan rejected executable content: {reason}` | 无 | **不能** |
| S8 | 新建技能 SKILL.md 判 `block`（[skills.py#L782-L784](file:///c:/projects/deer-flow/backend/app/gateway/routers/skills.py#L782-L784)） | **400** `Security scan blocked the create: {reason}` | 无 | **不能** |
| S9 | 回滚目标内容判 `block`（[skills.py#L1684-L1686](file:///c:/projects/deer-flow/backend/app/gateway/routers/skills.py#L1684-L1686)，先写 history 再抛） | **400** `Rollback blocked by security scanner: {reason}` | 无 | **不能** |
| S10 | 批量上传里文件非 UTF-8（[skills.py#L1438-L1452](file:///c:/projects/deer-flow/backend/app/gateway/routers/skills.py#L1438-L1452)） | 不报错，**按二进制直接写盘，完全跳过扫描** | —— | **不需要越过**（这是保存侧唯一的扫描缺口） |
| S11 | Agent 工具 `skill_manage`（[skill_manage_tool.py#L53-L59](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/tools/skill_manage_tool.py#L53-L59)） | 工具返回 `Security scan blocked the write: ...` | 无 | **不能**（Agent 侧，无用户按钮） |

> 保存侧**没有**文件大小限制、**没有**扩展名限制（`upload_custom_skill_files` 接受任意二进制并直接落盘）。这是与导入侧不对称的第二个缺口，但方向相反（更松）。

### 3.3 一句话点名

**除了"密钥"和"编码"，用户还会遇到**：frontmatter/name 格式与不匹配、缺 `SKILL.md`、嵌套 `SKILL.md`、同名技能已存在、归档路径穿越 / 非法 ZIP / 空归档 / 体积超限、上传非 `.zip|.skill`、写支撑文件时路径不在 `skills|references|templates|scripts|assets` 白名单下、技能不存在或无权限，以及（保存侧独有的）扫描模型抖动导致的偶发 `block`。

---

## 4. 边界讨论（含被推翻的结论与实测）

### 4.1 被推翻的结论

v1 曾主张：**"`scripts/**` 一律硬拦、绝不放行"**，理由是脚本会以服务进程身份执行任意代码。

该结论**不成立**，原因：

1. 业务技能的常见形态就是「`SKILL.md` 做入口说明 + `scripts/*.py` 调飞书/禅道/SRB 等外部系统」，脚本里放 `APP_SECRET`、`API_KEY` 是这类技能的**正常实现方式**，不是恶意特征。
2. 本次存量核查（§5）显示：自定义技能里 **6 个 `scripts/config.py` 硬编码凭据**、**16 个脚本含外部 URL**。如果按"可执行即硬拦"，这些技能全都无法创建、无法保存——等于让功能不可用。
3. 真正的攻击面差异不在"文件是否可执行"，而在"**代码在做什么**"：硬编码自己的 key 调已知 API ≠ 读 `~/.config` 里的 token 发到陌生 collector ≠ `exec(base64.b64decode(requests.get(...)))`。

**修正后的原则：按"判的是什么"划边界，不按文件类型划。**

### 4.2 实测方法与原始结果

临时探针（跑完已删除）：`scan_skill_content(content, executable=True, location="probe/<name>.py")`，5 个样本，每个最多重试 3 次。

| 样本 | 内容特征 | 实测 decision | 实测 reason（摘要） |
| --- | --- | --- | --- |
| `a_business_key` | `API_KEY = "sk-..."` + `requests.post(vendor_url, ...)` | **block**（历史上成功的一次）；其余几次因供应商 403 并发限制退化为兜底 block | `hardcoded live API secret`；兜底时 `Security scan unavailable for executable content; manual review required.` |
| `b_read_then_exfil` | 读 `~/.config/deerflow/token` 后 POST 到 collector | **block** | `Credential theft and exfiltration: reads a local auth token and posts it to an external collector endpoint.` |
| `c_obfuscated_exec` | `exec(base64.b64decode(requests.get(url).text))` | **block** | `Downloads and executes arbitrary remote code via exec(base64-decoded payload) from an external URL — classic second-stage payload execution / unsafe remote code execution.` |
| `d_syntax_error` | `def broken(:` + 少量正常调用 | 3 次均因 403 退化为兜底 **block**；模型可用时**无语法维度**，只能随机给 allow/warn | `Security scan unavailable ... manual review required.` |
| `e_env_clean` | `os.environ["VENDOR_API_KEY"]` + POST 到 vendor | **warn** | `Reads API key from environment and sends it to an external vendor API; benign integration pattern but involves credential handling and external network exfiltration risk` |

补充实测事实：模型供应商 403（`concurrent request limit`）会让 `scan_skill_content` 走到兜底分支，**可执行内容一律 `block`**。这本身就是用户会遇到的"误拦"，且 reason 与内容无关。

### 4.3 scanner 的区分度（明确结论）

| 场景 | scanner 判什么 | 命中哪条 rubric | 是否稳定 |
| --- | --- | --- | --- |
| (a) 脚本有密钥、正常调外部 API | 多为 `block`（`hardcoded live API secret`），偶发 `warn` | "exfiltration / unsafe executable code" + "borderline external API references" 之间摇摆 | **不稳定**，重复调用可翻转 |
| (b) 读环境变量/系统文件后外发 | `warn`（环境变量）／`block`（读本地凭据文件） | `borderline external API references` / `exfiltration` | 相对稳定，但取决于模型是否识别出"读的是本地凭据" |
| (c) 混淆、动态执行下载内容 | `block` | `unsafe executable code` | 稳定 |
| (d) 单纯语法错误 | **没有对应 rubric**，不做语法检查 | 无 | **判不了**；模型可用时可能 `allow/warn`，模型不可用时兜底 `block` |

**必须明确说的两句：**

- **(a) 与 (b) 区分不了。** 二者同为 `block` 或一 `block` 一 `warn`，差异只在 reason 措辞；没有确定性规则能把"正常业务外联"与"数据外泄"分开，(a) 甚至比 (b) 更容易被判 `block`。**因此不能靠 reason 文本自动放行。**
- **(d) 判不了。** rubric 里没有任何语法/可解析性维度，语法错误不会得到专门判定；它只会走语义判定或兜底 `block`。

**但 scanner 也确实区分了 (b) 与 (c)**：(c) 的 reason 明确指出"下载并执行远程代码/second-stage payload"，(b) 明确指出"credential theft and exfiltration"。所以"按判的是什么分类"在**展示层**是可行的（reason 足以让人判断），只是**不能当作自动决策依据**。

### 4.4 据此重划边界（每一类都对应 scanner 实际返回值）

分类只能用"**scanner 返回了什么**"来表达，不能用"文件在哪个目录"表达：

| 类别 | 对应 scanner 实际返回 | 处置 | 说明 |
| --- | --- | --- | --- |
| **可 force 放行（二次确认）** | `decision == "warn"`（任何 `executable`）；`decision == "block"` 且属于业务外联/硬编码凭据；`Security scan unavailable` 兜底（`executable` 分支） | 允许，但必须弹出一次明确确认，并写入 history | 这三类的共同点是"内容能跑、风险由用户自己的秘密承担"；`warn` 本来就是"borderline" |
| **硬拦不可放行** | 结构性错误：I1~I26、I34、S1~S3、S5；以及 `decision == "block"` 且 reason 指向 `credential theft` / `exfiltration` / `remote code execution` / `prompt injection` / `privilege escalation` | 直接拒绝 | 前一组是"写进去就是坏状态/坏路径"；后一组是"判定明确指向攻击行为" |
| **判不了（模型不可用 / 输出不可解析）** | `Security scan produced unparseable output; manual review required.`；`Security scan unavailable for executable content; ...`；`Security scan unavailable for skill content (model call failed); auto-approved ...` | **归"可 force 放行"**（因为它不代表内容有害，只代表没判成），但 reason 必须原样展示、文案标注"未能判定" | 若归硬拦，模型抖动会永久锁死用户；这正是当前保存侧的最大痛点 |
| **语法错误** | 无专属返回（可能 `allow`/`warn`/兜底 `block`） | 不用 scanner 管，交给 frontmatter 校验（S2/S3） | 不要在 scanner 里做语法检查 |

> 注意上表第 1 行与第 2 行之间存在**无法用代码判定的边界**（都可能是 `block`）。因此工程上只能实现成：
> `can_force = (decision != "allow")`，**由 force 弹窗的文案承担"让用户自己判断"的职责**，而不是试图自动分类。
> 这是"按判的是什么划"在当前实现下的最大可实现近似，必须在文档里写清，不能假装能精确区分。

---

## 5. 存量影响

核查对象：`c:\projects\deer-flow\skills`（仓库内技能根，`custom/` + `public/`）。

| 指标 | 数量 |
| --- | --- |
| `custom/` 技能数（排除 `.versions`/`.history`/`.owners` 等点目录） | **13** |
| `custom/` 文件总数 | 204 |
| `scripts/` 下实际脚本（`.py/.sh/.js`，排除 `.versions`） | **34** |
| 其中含外部 URL | **16** |
| 其中硬编码凭据（`api_key/app_secret/access_token/password/secret = "..."`） | **6** |
| `public/` 技能数 | 22（无脚本） |

含硬编码凭据的 6 个（全部是 `scripts/config.py`）：

- `feishu-approval-postaction/scripts/config.py`
- `feishu-approval-postaction-test/scripts/config.py`
- `feishu-table-approval/scripts/config.py`
- `feishu-table-approval-test/scripts/config.py`
- `project-management-agent/scripts/config.py`
- `srb-sync/scripts/config.py`

**这些技能今天靠什么过扫描？** 查 `.versions/<name>/index.jsonl` 与 `.history/<name>.jsonl`：

| 技能 | 版本快照中的 action | 结论 |
| --- | --- | --- |
| `feishu-approval-postaction` | `install` | 走导入，必然带 `force=true`（扫描被跳过） |
| `srb-sync` | `install` | 同上 |
| `feishu-table-approval-test` | `install` + `edit` | 导入时 force；后续编辑的是未含密钥的文件，才没被拦 |
| `feishu-table-approval` | 无任何版本/历史记录 | **从未走 API/编辑器**，是直接落到磁盘上的（开发期手工复制） |
| `feishu-approval-postaction-test` | 无记录 | 同上 |
| `project-management-agent` | 无记录 | 同上 |

即：**一半靠导入 force，一半根本没走产品链路。** 这解释了为什么用户"只见过导入侧的强制放行"——真实业务技能从来没有在编辑器里成功保存过带 key 的脚本。

---

## 6. 候选方案（分级）

### 6.1 最小切法（推荐，一版可收）

**目标**：只解决"编辑保存无出口"，不动导入、不动 scanner、不统一扫描范围。

| 改动 | 文件 | 量级 |
| --- | --- | --- |
| `update_custom_skill` 加 `force: bool = Query(False)`；`block` 时返回结构化 detail（`code="security_scan_blocked"`、`reason`、`can_force=True`），force 为真则继续写 | [skills.py#L1476-L1510](file:///c:/projects/deer-flow/backend/app/gateway/routers/skills.py#L1476-L1510) | +15~20 行 |
| `_write_custom_skill_support_file` 增加 `force` 参数（两处 400 都改成结构化）；`write_custom_skill_file` / `upload_custom_skill_files` 透传 | [skills.py#L1272-L1303](file:///c:/projects/deer-flow/backend/app/gateway/routers/skills.py#L1272-L1303) | +20~25 行 |
| 新增 `SkillWriteErrorDetail`（或复用 `SkillUploadErrorDetail`） | 同文件 L150 附近 | +6 行 |
| `updateCustomSkill` / `writeCustomSkillFile` 支持 `options.force`，错误解析改用 `readErrorData` 并保留 `can_force` | [api.ts#L297-L318](file:///c:/projects/deer-flow/frontend/src/core/skills/api.ts#L297-L318)、[L383-L403](file:///c:/projects/deer-flow/frontend/src/core/skills/api.ts#L383-L403) | +25~35 行 |
| `applyChanges` 捕获 `canForce` → `window.confirm`（复用同文件既有 confirm 模式）→ 带 force 重试该文件；否则保持现状 | [skill-editor-workspace.tsx#L1113-L1173](file:///c:/projects/deer-flow/frontend/src/components/workspace/skills/editor/skill-editor-workspace.tsx#L1113-L1173) | +25~35 行 |

- 文件数：**3 个**（`backend/app/gateway/routers/skills.py`、`frontend/src/core/skills/api.ts`、`frontend/src/components/.../skill-editor-workspace.tsx`）。
- 行数：**70~110 行**（不含测试）。
- 风险：**中**。因为 `can_force` 若按"`block` 即可 force"实现，等于把 (b)/(c) 也放开了；若按"`block` 且 reason 不含 exfiltration/RCE"实现，则依赖不可靠的文本匹配。**推荐前者 + 强文案**（文案必须写明"脚本会以本机服务身份运行、可读环境变量、无网络隔离"）。
- 要不要碰导入侧：**不要**。导入侧今天已能 force，改动会带来回归；两侧唯一需要对齐的是错误结构（前端已能解析，保存侧复用即可）。
- 是否改 scanner：**不改**。rubric 一改，导入侧所有已 force 的技能行为都会变。
- 遗留不一致（可接受）：导入 force 跳过全部扫描，保存 force 只跳过"被拦的那一次"；`scripts/` 的 `warn` 导入可 force、保存也可 force，这次算对齐了。

### 6.2 完全统一（后续排期）

| 项目 | 内容 | 改动面 | 风险 |
| --- | --- | --- | --- |
| 统一扫描范围 | 保存侧也按导入侧白名单扫（`scripts/**` + `references/templates` 的文本后缀），`assets/` 文本也扫 | `skills.py` 4 处 + 新增 helper | 会出现"以前能存、现在存不了"的回归，需要灰度 |
| 统一 force 语义 | 把导入的 `skip_security_scan` 从"跳过全部"改成"只跳过 `can_force` 项"（`_scan_skill_file_or_raise(..., allow_force=force)`） | `installer.py` L202-L247 | **收紧**，少数靠 force 装脚本的技能会失败，必须单独确认 |
| 统一错误结构 | 四处硬拦（create / update / write file / rollback）+ agent 工具全部返回 `{code, reason, can_force}`；前端统一 `readErrorData` | 4 个后端文件 + 2 个前端文件 | 前端所有调用点都要改，测试面大 |
| 补 history | 导入 force 时在版本 `message` 标注 `forced`；保存 force 时 history 加 `"forced": true` | `skills.py` + `installer.py` | 低 |
| 预扫描再落盘 | 编辑保存改为"先全量扫描，全部通过再写"，避免 SKILL.md 已写、脚本被拦的半成功态 | `skills.py` applyChanges 链路 | 中，需要重排写入顺序 |

---

## 7. 放行后的运行时后果（必须写进确认文案）

- **执行身份**：本地沙箱 `execute_command` 走 `subprocess.run(..., shell=False, env=None)`（[local_sandbox.py#L311-L355](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/sandbox/local/local_sandbox.py#L311-L355)），与网关**同一 OS 用户**，**继承全部环境变量**（含 `KIMI_API_KEY`、`DATABASE_URL` 等），**无网络隔离**，超时 600s。
- 因此一个被放行的 `scripts/config.py` 不仅能拿到自己的 key，还能读 `os.environ` 里的全部服务密钥并外发；也能读 `skills/custom` 下其他技能的文件。
- **凭据是否进提示词**：技能的 `SKILL.md` **正文不会**自动进入系统提示词——[prompt.py#L540-L576](file:///c:/projects/deer-flow/backend/packages/harness/deerflow/agents/lead_agent/prompt.py#L540-L576) 只输出 `<name>/<description>/<location>/<connector_requirements>`，正文靠 agent 主动 `read_file` 渐进加载。**但只要 agent 读了脚本，脚本内容就会作为工具结果进入上下文并发给模型服务商**（含硬编码的 key）。这一点比"正文进提示词"更隐蔽，确认文案应写明。
- 导入 force 与保存 force 在运行时**没有区别**：都只是把文件写到 `skills/custom/<name>/` 下，之后由 agent/沙箱按需执行。

---

## 8. 本次不实施

**本轮不改任何代码与测试。** 本文只作为取证与决策存档，供后续排期：

1. 先做 §6.1 最小切法（仅保存侧出口）。
2. 再评估 §6.2 的扫描范围对齐与 force 语义收紧（会改变导入行为，需单独确认 + 回归）。
3. 若长期仍靠 LLM 判定，建议评估"确定性规则"补充（如正则识别 `sk-`/`APP_SECRET` 字面量）以降低不可复现性——**不要**直接扩 rubric，会同时改变两侧行为。

---

## 附录 A：实测原始输出（节选）

```
[a_business_key]     decision=block  reason=hardcoded live API secret        (另几次为 403 兜底 block)
[b_read_then_exfil]  decision=block  reason=Credential theft and exfiltration: reads a local auth token and posts it to an external collector endpoint.
[c_obfuscated_exec]  decision=block  reason=Downloads and executes arbitrary remote code via exec(base64-decoded payload) from an external URL ...
[d_syntax_error]     decision=block  reason=Security scan unavailable for executable content; manual review required.  (403 兜底)
[e_env_clean]        decision=warn   reason=Reads API key from environment and sends it to an external vendor API; benign integration pattern ...
```

## 附录 B：v1 → v2 变更摘要

| 项 | v1 | v2 |
| --- | --- | --- |
| `scripts/**` 边界 | 一律硬拦、绝不放行 | **推翻**：按"判的是什么"划；业务脚本允许 force，攻击语义硬拦 |
| scanner 区分能力 | 未实测 | 已实测 (a)~(e)，结论：能区分 (b)/(c)，**不能**区分 (a)/(b)，**判不了** (d) |
| 拦截清单 | 只列主要几条 | 导入 35 条 + 保存 11 条，含行号、code、文案、can_force、force 后能否过 |
| 存量影响 | 无 | 13 技能 / 34 脚本 / 16 含外链 / 6 含硬编码凭据，含各自来源溯源 |
| "SKILL.md 进提示词" | 表述错误 | 已更正：只注入元数据，正文按需 `read_file` |
| 方案 | 单一最小改法 | 最小切法 + 完全统一两级，含改动面与风险 |
