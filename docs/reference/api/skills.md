# 技能 Skills

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/skills`
> 认证：需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`GET`](#get-api-skills) | `/api/skills` | List All Skills |
| [`GET`](#get-api-skills-custom) | `/api/skills/custom` | List Custom Skills |
| [`POST`](#post-api-skills-custom) | `/api/skills/custom` | Create Custom Skill |
| [`POST`](#post-api-skills-custom-ai-draft) | `/api/skills/custom/ai-draft` | Draft Custom Skill With AI |
| [`GET`](#get-api-skills-custom-skill-name) | `/api/skills/custom/{skill_name}` | Get Custom Skill Content |
| [`PUT`](#put-api-skills-custom-skill-name) | `/api/skills/custom/{skill_name}` | Edit Custom Skill |
| [`DELETE`](#delete-api-skills-custom-skill-name) | `/api/skills/custom/{skill_name}` | Delete Custom Skill |
| [`POST`](#post-api-skills-custom-skill-name-directories) | `/api/skills/custom/{skill_name}/directories` | Create Custom Skill Directory |
| [`GET`](#get-api-skills-custom-skill-name-file) | `/api/skills/custom/{skill_name}/file` | Read Custom Skill File |
| [`PUT`](#put-api-skills-custom-skill-name-file) | `/api/skills/custom/{skill_name}/file` | Write Custom Skill File |
| [`DELETE`](#delete-api-skills-custom-skill-name-file) | `/api/skills/custom/{skill_name}/file` | Delete Custom Skill File |
| [`GET`](#get-api-skills-custom-skill-name-files) | `/api/skills/custom/{skill_name}/files` | List Custom Skill Files |
| [`GET`](#get-api-skills-custom-skill-name-history) | `/api/skills/custom/{skill_name}/history` | Get Custom Skill History |
| [`POST`](#post-api-skills-custom-skill-name-rollback) | `/api/skills/custom/{skill_name}/rollback` | Rollback Custom Skill |
| [`POST`](#post-api-skills-custom-skill-name-upload) | `/api/skills/custom/{skill_name}/upload` | Upload Custom Skill Files |
| [`GET`](#get-api-skills-custom-skill-name-versions) | `/api/skills/custom/{skill_name}/versions` | List Custom Skill Versions |
| [`POST`](#post-api-skills-custom-skill-name-versions) | `/api/skills/custom/{skill_name}/versions` | Create Custom Skill Version Snapshot |
| [`GET`](#get-api-skills-custom-skill-name-versions-seq-file) | `/api/skills/custom/{skill_name}/versions/{seq}/file` | Read Custom Skill Version File |
| [`GET`](#get-api-skills-custom-skill-name-versions-seq-files) | `/api/skills/custom/{skill_name}/versions/{seq}/files` | List Custom Skill Version Files |
| [`POST`](#post-api-skills-custom-skill-name-versions-seq-restore) | `/api/skills/custom/{skill_name}/versions/{seq}/restore` | Restore Custom Skill Version |
| [`POST`](#post-api-skills-install) | `/api/skills/install` | Install Skill |
| [`GET`](#get-api-skills-public-skill-name) | `/api/skills/public/{skill_name}` | Get Public Skill Content (Admin) |
| [`POST`](#post-api-skills-upload) | `/api/skills/upload` | Upload Skill Archive |
| [`GET`](#get-api-skills-skill-name) | `/api/skills/{skill_name}` | Get Skill Details |
| [`PUT`](#put-api-skills-skill-name) | `/api/skills/{skill_name}` | Update Skill |

## `GET /api/skills`

> List All Skills  
<a id="get-api-skills"></a>

Retrieve a list of all available skills from both public and custom directories.

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`SkillsListResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/skills'
```

---

## `GET /api/skills/custom`

> List Custom Skills  
<a id="get-api-skills-custom"></a>

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`SkillsListResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/skills/custom'
```

---

## `POST /api/skills/custom`

> Create Custom Skill  
<a id="post-api-skills-custom"></a>

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Hyphen-case custom skill name |
| `description` | string | 是 | Short skill description |
| `content` | string | 否 | Optional SKILL.md content. If omitted, a starter document is generated. |
| `allowed_tools` | array<string> | 否 | Optional tool names to mention in the starter SKILL.md |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`CustomSkillContentResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/skills/custom'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `POST /api/skills/custom/ai-draft`

> Draft Custom Skill With AI  
<a id="post-api-skills-custom-ai-draft"></a>

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `prompt` | string | 是 | User brief for the skill to draft |
| `name_hint` | string | 否 | Optional hyphen-case skill name hint |
| `description_hint` | string | 否 | Optional short description hint |
| `deep_thinking` | boolean | 否 | Whether to request a more deliberate draft |
| `skill_creator_name` | string | 否 | Optional creator profile name |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`SkillAIDraftResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/skills/custom/ai-draft'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `GET /api/skills/custom/{skill_name}`

> Get Custom Skill Content  
<a id="get-api-skills-custom-skill-name"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `skill_name` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`CustomSkillContentResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/skills/custom/{skill_name}'
```

---

## `PUT /api/skills/custom/{skill_name}`

> Edit Custom Skill  
<a id="put-api-skills-custom-skill-name"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `skill_name` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `content` | string | 是 | Replacement SKILL.md content |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`CustomSkillContentResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X PUT '${DEERFLOW_BASE_URL}/api/skills/custom/{skill_name}'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `DELETE /api/skills/custom/{skill_name}`

> Delete Custom Skill  
<a id="delete-api-skills-custom-skill-name"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `skill_name` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`object`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X DELETE '${DEERFLOW_BASE_URL}/api/skills/custom/{skill_name}'
```

---

## `POST /api/skills/custom/{skill_name}/directories`

> Create Custom Skill Directory  
<a id="post-api-skills-custom-skill-name-directories"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `skill_name` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `path` | string | 是 | Relative directory path from the skill root |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`CustomSkillFileEntry`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/skills/custom/{skill_name}/directories'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `GET /api/skills/custom/{skill_name}/file`

> Read Custom Skill File  
<a id="get-api-skills-custom-skill-name-file"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `skill_name` | string | 是 |  |

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `path` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`CustomSkillFileContentResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/skills/custom/{skill_name}/file'
```

---

## `PUT /api/skills/custom/{skill_name}/file`

> Write Custom Skill File  
<a id="put-api-skills-custom-skill-name-file"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `skill_name` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `path` | string | 是 | Relative path from the skill root |
| `content` | string | 否 | File text content |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`CustomSkillFileContentResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X PUT '${DEERFLOW_BASE_URL}/api/skills/custom/{skill_name}/file'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `DELETE /api/skills/custom/{skill_name}/file`

> Delete Custom Skill File  
<a id="delete-api-skills-custom-skill-name-file"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `skill_name` | string | 是 |  |

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `path` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`object`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X DELETE '${DEERFLOW_BASE_URL}/api/skills/custom/{skill_name}/file'
```

---

## `GET /api/skills/custom/{skill_name}/files`

> List Custom Skill Files  
<a id="get-api-skills-custom-skill-name-files"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `skill_name` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`CustomSkillFilesResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/skills/custom/{skill_name}/files'
```

---

## `GET /api/skills/custom/{skill_name}/history`

> Get Custom Skill History  
<a id="get-api-skills-custom-skill-name-history"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `skill_name` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`CustomSkillHistoryResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/skills/custom/{skill_name}/history'
```

---

## `POST /api/skills/custom/{skill_name}/rollback`

> Rollback Custom Skill  
<a id="post-api-skills-custom-skill-name-rollback"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `skill_name` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `history_index` | integer | 否 | History entry index to restore from, defaulting to the latest change. |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`CustomSkillContentResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/skills/custom/{skill_name}/rollback'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `POST /api/skills/custom/{skill_name}/upload`

> Upload Custom Skill Files  
<a id="post-api-skills-custom-skill-name-upload"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `skill_name` | string | 是 |  |

**请求体**（`multipart/form-data`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `files` | array<string> | 是 | Files |
| `paths` | array<string> | 是 | Paths |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`CustomSkillUploadResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/skills/custom/{skill_name}/upload'
```

---

## `GET /api/skills/custom/{skill_name}/versions`

> List Custom Skill Versions  
<a id="get-api-skills-custom-skill-name-versions"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `skill_name` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`CustomSkillVersionsResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/skills/custom/{skill_name}/versions'
```

---

## `POST /api/skills/custom/{skill_name}/versions`

> Create Custom Skill Version Snapshot  
<a id="post-api-skills-custom-skill-name-versions"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `skill_name` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `action` | string | 否 | Version action label, e.g. edit/publish/install/create/restore. |
| `message` | string | 否 | Optional human note for this snapshot. |
| `thread_id` | string | 否 | Optional thread id that produced this snapshot. |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`object`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/skills/custom/{skill_name}/versions'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `GET /api/skills/custom/{skill_name}/versions/{seq}/file`

> Read Custom Skill Version File  
<a id="get-api-skills-custom-skill-name-versions-seq-file"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `skill_name` | string | 是 |  |
| `seq` | integer | 是 |  |

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `path` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`CustomSkillFileContentResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/skills/custom/{skill_name}/versions/{seq}/file'
```

---

## `GET /api/skills/custom/{skill_name}/versions/{seq}/files`

> List Custom Skill Version Files  
<a id="get-api-skills-custom-skill-name-versions-seq-files"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `skill_name` | string | 是 |  |
| `seq` | integer | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`CustomSkillFilesResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/skills/custom/{skill_name}/versions/{seq}/files'
```

---

## `POST /api/skills/custom/{skill_name}/versions/{seq}/restore`

> Restore Custom Skill Version  
<a id="post-api-skills-custom-skill-name-versions-seq-restore"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `skill_name` | string | 是 |  |
| `seq` | integer | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`CustomSkillVersionRestoreResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/skills/custom/{skill_name}/versions/{seq}/restore'
```

---

## `POST /api/skills/install`

> Install Skill  
<a id="post-api-skills-install"></a>

Install a skill from a .skill file (ZIP archive) located in the thread's user-data directory.

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 | The thread ID where the .skill file is located |
| `path` | string | 是 | Virtual path to the .skill file (e.g., mnt/user-data/outputs/my-skill.skill) |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`SkillInstallResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/skills/install'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `GET /api/skills/public/{skill_name}`

> Get Public Skill Content (Admin)  
<a id="get-api-skills-public-skill-name"></a>

Read SKILL.md for a public skill. Restricted to admin users.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `skill_name` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`CustomSkillContentResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/skills/public/{skill_name}'
```

---

## `POST /api/skills/upload`

> Upload Skill Archive  
<a id="post-api-skills-upload"></a>

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `force` | boolean | 否 | Skip user-overridable skill security scan checks after explicit user confirmation. |

**请求体**（`multipart/form-data`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `file` | string | 是 | File |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`SkillInstallResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/skills/upload'
```

---

## `GET /api/skills/{skill_name}`

> Get Skill Details  
<a id="get-api-skills-skill-name"></a>

Retrieve detailed information about a specific skill by its name.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `skill_name` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`SkillResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/skills/{skill_name}'
```

---

## `PUT /api/skills/{skill_name}`

> Update Skill  
<a id="put-api-skills-skill-name"></a>

Update a skill's enabled status by modifying the extensions_config.json file. Toggling public skills requires admin access.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `skill_name` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `enabled` | boolean | 是 | Whether to enable or disable the skill |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`SkillResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X PUT '${DEERFLOW_BASE_URL}/api/skills/{skill_name}'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## 数据模型

### `Body_upload_custom_skill_files_api_skills_custom__skill_name__upload_post`

Body_upload_custom_skill_files_api_skills_custom__skill_name__upload_post

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `files` | array<string> | 是 | Files |
| `paths` | array<string> | 是 | Paths |

### `Body_upload_skill_archive_api_skills_upload_post`

Body_upload_skill_archive_api_skills_upload_post

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `file` | string | 是 | File |

### `CustomSkillContentResponse`

CustomSkillContentResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Name of the skill |
| `description` | string | 是 | Description of what the skill does |
| `display_name` | string | 否 | Display name of the skill (e.g. Chinese name) |
| `description_zh` | string | 否 | Chinese description of the skill |
| `license` | string | 否 | License information |
| `category` | SkillCategory | 是 | Category of the skill (public or custom) |
| `enabled` | boolean | 否 | Whether this skill is enabled |
| `content` | string | 是 | Raw SKILL.md content |

### `CustomSkillCreateRequest`

CustomSkillCreateRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Hyphen-case custom skill name |
| `description` | string | 是 | Short skill description |
| `content` | string | 否 | Optional SKILL.md content. If omitted, a starter document is generated. |
| `allowed_tools` | array<string> | 否 | Optional tool names to mention in the starter SKILL.md |

### `CustomSkillDirectoryCreateRequest`

CustomSkillDirectoryCreateRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `path` | string | 是 | Relative directory path from the skill root |

### `CustomSkillFileContentResponse`

CustomSkillFileContentResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `path` | string | 是 | Relative path from the skill root |
| `content` | string | 是 | File text content |

### `CustomSkillFileEntry`

CustomSkillFileEntry

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `path` | string | 是 | Relative path from the skill root |
| `type` | string | 是 | Either file or directory |
| `size` | integer | 否 | File size in bytes |

### `CustomSkillFileWriteRequest`

CustomSkillFileWriteRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `path` | string | 是 | Relative path from the skill root |
| `content` | string | 否 | File text content |

### `CustomSkillFilesResponse`

CustomSkillFilesResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `files` | array<CustomSkillFileEntry> | 是 | Files |

### `CustomSkillHistoryResponse`

CustomSkillHistoryResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `history` | array<object> | 是 | History |

### `CustomSkillUpdateRequest`

CustomSkillUpdateRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `content` | string | 是 | Replacement SKILL.md content |

### `CustomSkillUploadResponse`

CustomSkillUploadResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `paths` | array<string> | 否 | Uploaded relative paths |

### `CustomSkillVersionCreateRequest`

CustomSkillVersionCreateRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `action` | string | 否 | Version action label, e.g. edit/publish/install/create/restore. |
| `message` | string | 否 | Optional human note for this snapshot. |
| `thread_id` | string | 否 | Optional thread id that produced this snapshot. |

### `CustomSkillVersionRestoreResponse`

CustomSkillVersionRestoreResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `version` | object | 是 | Created version record representing the restored state. |

### `CustomSkillVersionsResponse`

CustomSkillVersionsResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `versions` | array<object> | 是 | Versions |

### `HTTPValidationError`

HTTPValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `detail` | array<ValidationError> | 否 | Detail |

### `SkillAIDraftRequest`

SkillAIDraftRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `prompt` | string | 是 | User brief for the skill to draft |
| `name_hint` | string | 否 | Optional hyphen-case skill name hint |
| `description_hint` | string | 否 | Optional short description hint |
| `deep_thinking` | boolean | 否 | Whether to request a more deliberate draft |
| `skill_creator_name` | string | 否 | Optional creator profile name |

### `SkillAIDraftResponse`

SkillAIDraftResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Suggested hyphen-case skill name |
| `description` | string | 是 | Suggested skill description |
| `content` | string | 是 | Generated SKILL.md draft |

### `SkillCategory`

Source category for a skill.

- ``PUBLIC``: built-in skill bundled with the platform, read-only.
- ``CUSTOM``: user-authored skill that can be edited or deleted.

（无固定字段 / 任意结构）

### `SkillInstallRequest`

Request model for installing a skill from a .skill file.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 | The thread ID where the .skill file is located |
| `path` | string | 是 | Virtual path to the .skill file (e.g., mnt/user-data/outputs/my-skill.skill) |

### `SkillInstallResponse`

Response model for skill installation.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `success` | boolean | 是 | Whether the installation was successful |
| `skill_name` | string | 是 | Name of the installed skill |
| `message` | string | 是 | Installation result message |

### `SkillResponse`

Response model for skill information.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Name of the skill |
| `description` | string | 是 | Description of what the skill does |
| `display_name` | string | 否 | Display name of the skill (e.g. Chinese name) |
| `description_zh` | string | 否 | Chinese description of the skill |
| `license` | string | 否 | License information |
| `category` | SkillCategory | 是 | Category of the skill (public or custom) |
| `enabled` | boolean | 否 | Whether this skill is enabled |

### `SkillRollbackRequest`

SkillRollbackRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `history_index` | integer | 否 | History entry index to restore from, defaulting to the latest change. |

### `SkillUpdateRequest`

Request model for updating a skill.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `enabled` | boolean | 是 | Whether to enable or disable the skill |

### `SkillsListResponse`

Response model for listing all skills.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `skills` | array<SkillResponse> | 是 | Skills |

### `ValidationError`

ValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `loc` | array<string | integer> | 是 | Location |
| `msg` | string | 是 | Message |
| `type` | string | 是 | Error Type |
| `input` |  | 否 | Input |
| `ctx` | object | 否 | Context |
