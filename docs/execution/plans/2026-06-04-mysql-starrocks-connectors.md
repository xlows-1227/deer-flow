# MySQL And StarRocks Connectors Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the first version of the DeerFlow Connector Platform with read-only MySQL and StarRocks database connectors.

**Architecture:** Add a platform-level connector subsystem under `deerflow.connectors` with typed connector definitions, dynamic connector instances persisted in the DeerFlow app database, secret references, policy enforcement, audit logging, and a small set of runtime tools. MySQL and StarRocks should share a database connector base while keeping dialect-specific connection, introspection, and SQL validation behavior separate.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy async ORM for DeerFlow persistence, `asyncmy` or equivalent MySQL-compatible async driver for connector execution, `sqlglot` or equivalent SQL AST parser, existing DeerFlow tool/guardrails/user-context infrastructure, SQLite/Postgres for DeerFlow app persistence.

---

## First Version Scope

Build:

- Connector type registry for `mysql` and `starrocks`.
- Dynamic connector instance CRUD API.
- `env` secret provider first; add `encrypted_db` only if product requires UI-entered passwords in v1.
- Database read-only policy model.
- Connection test and schema introspection.
- Runtime tools: `list_connectors`, `inspect_connector`, `query_database`, `sample_database_table`.
- Basic grants for `user`, `skill`, `agent`, and `thread`.
- Audit log for allow/deny/query/test/introspect actions.
- Code-level SQL safety validation.
- Backend tests covering persistence, policy, SQL safety, adapters, tools, and gateway routers.

Do not build:

- Write SQL, DDL, ETL, scheduled sync, OAuth, document connectors, OpenAPI connectors, approval flow, or team sharing.
- Direct exposure of raw host, username, password, token, or connection URL to the model.
- Per-connector dynamic LangChain tool generation.

Key product assumption:

- StarRocks is treated as a first-class connector type, not just `type=mysql` with another name. It can use MySQL-compatible wire protocol, but policy, introspection, error handling, type normalization, and SQL validation should know the connector is `starrocks`.

---

## Architecture Decisions

### Decision 1: Connector Platform Is The Product Abstraction

Use `ConnectorInstance` as the user-facing resource and `SecretRef` as the internal credential boundary. Do not reuse `config.yaml database:` because that config is DeerFlow internal state storage.

### Decision 2: Database Connector Base With Dialect Modules

Create one shared database adapter base for connection lifecycle, query result shaping, max rows, timeout, redaction hooks, and audit summaries. Add `MySQLConnectorAdapter` and `StarRocksConnectorAdapter` for:

- SQLAlchemy/driver URL construction.
- Dialect name passed to SQL parser.
- Introspection queries.
- Type normalization.
- Read-only session setup.
- Engine/session settings.

### Decision 3: API-First MVP, UI Second

Implement complete backend APIs first. Add frontend management UI after the API and runtime tools are stable. This keeps security behavior testable before building forms.

### Decision 4: Secret Provider Starts Narrow

Start with `env` SecretStore because it avoids storing new sensitive material in the app DB. The API can accept `credential.provider=env` and `credential.ref=MY_DB_URL` or split username/password refs. Add `encrypted_db` in a later milestone only if v1 requires users to paste credentials into the UI.

### Decision 5: SQL Safety Is Deny-By-Default

The LLM prompt is not a security boundary. `query_database` must parse SQL, reject non-read statements, reject multi-statements, enforce allowlists/blocklists, apply limits, apply timeout, and execute with a read-only database principal whenever possible.

---

## Proposed Backend File Layout

Create:

```text
backend/packages/harness/deerflow/connectors/
  __init__.py
  audit.py
  errors.py
  policy.py
  registry.py
  resources.py
  schemas.py
  secrets.py
  service.py
  sql_safety.py
  tools.py
  adapters/
    __init__.py
    base.py
    database.py
    mysql.py
    starrocks.py

backend/packages/harness/deerflow/config/
  connectors_config.py

backend/packages/harness/deerflow/persistence/connector/
  __init__.py
  model.py
  sql.py

backend/app/gateway/routers/
  connectors.py
```

Modify:

```text
backend/packages/harness/deerflow/persistence/models/__init__.py
backend/packages/harness/deerflow/config/app_config.py
backend/packages/harness/deerflow/tools/tools.py
backend/app/gateway/app.py
backend/pyproject.toml
backend/packages/harness/pyproject.toml
config.example.yaml
```

Tests:

```text
backend/tests/test_connectors_config.py
backend/tests/test_connectors_models.py
backend/tests/test_connectors_repository.py
backend/tests/test_connectors_registry.py
backend/tests/test_connectors_policy.py
backend/tests/test_connectors_secrets.py
backend/tests/test_connectors_sql_safety.py
backend/tests/test_mysql_connector_adapter.py
backend/tests/test_starrocks_connector_adapter.py
backend/tests/test_connectors_tools.py
backend/tests/test_connectors_router.py
backend/tests/test_connectors_user_isolation.py
backend/tests/test_connectors_audit.py
```

---

## Data Model

### `connector_instances`

```text
id                  string(64), primary key
tenant_id           string(64), nullable, indexed
owner_id            string(64), nullable, indexed
name                string(80), required
display_name        string(160), nullable
type                string(40), required, indexed: mysql/starrocks
status              string(20), required: active/disabled/deleted/error
config_json         json, required
credential_ref      string(128), required
default_policy_json json, required
health_json         json, required default {}
last_tested_at      datetime, nullable
last_used_at        datetime, nullable
created_at          datetime, required
updated_at          datetime, required
deleted_at          datetime, nullable
```

Constraints:

```text
unique(owner_id, name) for non-deleted rows when possible
index(owner_id, type, status)
index(tenant_id, type, status)
```

SQLite cannot do the same partial unique index portably. For MVP, enforce unique active names in repository code and use broad DB indexes.

### `connector_grants`

```text
id                   string(64), primary key
connector_id          string(64), indexed
subject_type          string(24): user/skill/agent/thread
subject_id            string(128), required
capabilities_json     json, required
policy_override_json  json, required default {}
expires_at            datetime, nullable
created_by            string(64), nullable
created_at            datetime, required
updated_at            datetime, required
```

### `connector_metadata_cache`

```text
id                  string(64), primary key
connector_id         string(64), indexed
resource_type        string(40): schema/table/columns/sample
metadata_json        json, required
cached_at            datetime, required
expires_at           datetime, nullable
```

### `connector_audit_logs`

```text
id                  integer, primary key autoincrement
connector_id         string(64), indexed
connector_type       string(40), indexed
user_id              string(64), nullable, indexed
tenant_id            string(64), nullable, indexed
thread_id            string(64), nullable, indexed
run_id               string(64), nullable, indexed
agent_id             string(128), nullable
skill_name           string(128), nullable
capability           string(80), required
operation            string(40), required
decision             string(20), required: allow/deny/error
request_summary_json json, required
result_summary_json  json, required
error_code           string(120), nullable
elapsed_ms           integer, nullable
created_at           datetime, required, indexed
```

Audit rule:

- Store SQL hash, normalized SQL preview, referenced tables, row count, elapsed time, and denial reason.
- Do not store passwords, tokens, full connection URLs, or raw result rows.
- Default to not storing full SQL. Store full SQL only behind a later explicit config flag after redaction exists.

---

## Public API

Add router `backend/app/gateway/routers/connectors.py` with prefix `/api`.

Connector types:

```http
GET /api/connector-types
GET /api/connector-types/{type}
```

Connector instances:

```http
POST /api/connectors
GET /api/connectors
GET /api/connectors/{connector_id}
PATCH /api/connectors/{connector_id}
DELETE /api/connectors/{connector_id}
POST /api/connectors/{connector_id}/disable
POST /api/connectors/{connector_id}/enable
```

Database operations:

```http
POST /api/connectors/{connector_id}/test
POST /api/connectors/{connector_id}/introspect
GET /api/connectors/{connector_id}/resources
GET /api/connectors/{connector_id}/schema
POST /api/connectors/{connector_id}/query
POST /api/connectors/{connector_id}/sample
```

Grants:

```http
POST /api/connectors/{connector_id}/grants
GET /api/connectors/{connector_id}/grants
PATCH /api/connectors/{connector_id}/grants/{grant_id}
DELETE /api/connectors/{connector_id}/grants/{grant_id}
```

Audit:

```http
GET /api/connectors/{connector_id}/audit
GET /api/connector-audit
```

Create connector request, MySQL:

```json
{
  "name": "prod_orders_mysql",
  "display_name": "Production Orders MySQL",
  "type": "mysql",
  "config": {
    "host": "mysql.internal",
    "port": 3306,
    "database": "orders",
    "ssl": true
  },
  "credential": {
    "provider": "env",
    "ref": "PROD_ORDERS_MYSQL_URL"
  },
  "default_policy": {
    "mode": "read_only",
    "allowed_schemas": ["orders", "mart"],
    "blocked_tables": ["users_passwords", "payment_cards"],
    "max_rows": 5000,
    "statement_timeout_ms": 30000,
    "require_limit": true,
    "pii_policy": "mask"
  }
}
```

Create connector request, StarRocks:

```json
{
  "name": "ads_starrocks",
  "display_name": "ADS StarRocks",
  "type": "starrocks",
  "config": {
    "host": "starrocks-fe.internal",
    "query_port": 9030,
    "database": "ads",
    "ssl": false
  },
  "credential": {
    "provider": "env",
    "ref": "ADS_STARROCKS_URL"
  },
  "default_policy": {
    "mode": "read_only",
    "allowed_schemas": ["ads", "dim"],
    "max_rows": 10000,
    "statement_timeout_ms": 30000,
    "require_limit": true
  }
}
```

---

## Runtime Tools

Add to `deerflow.connectors.tools` and expose through `get_available_tools()` when `connectors.enabled=true`.

Tool names:

```text
list_connectors
inspect_connector
query_database
sample_database_table
```

Tool behavior:

- `list_connectors(capability=None)` returns only safe summaries and only connectors allowed in the current runtime context.
- `inspect_connector(connector_id, resource_type="schema")` returns cached schema metadata, refreshing only through explicit API/tool argument if allowed.
- `query_database(connector_id, sql, reason)` performs authz, policy merge, SQL safety validation, adapter execution, result shaping, audit logging, and user-friendly errors.
- `sample_database_table(connector_id, schema, table, limit=20)` builds SQL internally rather than trusting model-generated SQL.

Do not expose:

- Host.
- Port.
- Username.
- Password.
- Token.
- Raw connection URL.
- Full config JSON.

---

## MySQL And StarRocks Differences To Handle

Shared:

- Use MySQL-compatible connection parameters where possible.
- Support `database.query`, `database.schema.inspect`, `database.table.sample`.
- Return normalized `columns`, `rows`, `row_count`, `truncated`, `elapsed_ms`.
- Enforce `max_rows`, `require_limit`, `allowed_schemas`, `blocked_tables`, `statement_timeout_ms`.

MySQL:

- Default port `3306`.
- Introspection via `information_schema.schemata`, `information_schema.tables`, `information_schema.columns`.
- Read-only transaction can use `START TRANSACTION READ ONLY` where supported.
- Timeout can use session variable strategy where available, plus driver-level timeout.

StarRocks:

- Default query port `9030`.
- Treat StarRocks FE as the endpoint.
- Introspection should prefer StarRocks-compatible `information_schema` queries, with adapter tests covering expected SQL strings and result mapping.
- Avoid assuming OLTP features such as row-level locks or ordinary transaction semantics.
- Use query timeout/session variables if available in target deployment; otherwise enforce client-side timeout and cancellation best effort.
- Treat `SHOW`/admin statements as denied in v1 even if StarRocks users often use them operationally.

---

## Task 1: Add Connector Config

**Files:**

- Create: `backend/packages/harness/deerflow/config/connectors_config.py`
- Modify: `backend/packages/harness/deerflow/config/app_config.py`
- Modify: `config.example.yaml`
- Test: `backend/tests/test_connectors_config.py`

**Steps:**

1. Write tests for default disabled/enabled config loading.
2. Add `ConnectorsConfig` with fields: `enabled`, `secret_store.provider`, `default_policy.database`, `enabled_types`.
3. Wire it into `AppConfig`.
4. Add `connectors:` example to `config.example.yaml`.
5. Run:

```powershell
cd backend
uv run pytest tests/test_connectors_config.py tests/test_app_config_reload.py -q
```

---

## Task 2: Add Persistence Models

**Files:**

- Create: `backend/packages/harness/deerflow/persistence/connector/__init__.py`
- Create: `backend/packages/harness/deerflow/persistence/connector/model.py`
- Modify: `backend/packages/harness/deerflow/persistence/models/__init__.py`
- Test: `backend/tests/test_connectors_models.py`

**Steps:**

1. Write tests that assert `Base.metadata.tables` contains connector tables.
2. Implement SQLAlchemy ORM rows for instances, grants, metadata cache, and audit logs.
3. Register models in `persistence/models/__init__.py`.
4. Keep all enums as strings for SQLite/Postgres portability.
5. Run:

```powershell
cd backend
uv run pytest tests/test_connectors_models.py tests/test_persistence_scaffold.py -q
```

---

## Task 3: Add Repository Layer

**Files:**

- Create: `backend/packages/harness/deerflow/persistence/connector/sql.py`
- Test: `backend/tests/test_connectors_repository.py`
- Test: `backend/tests/test_connectors_user_isolation.py`

**Steps:**

1. Write tests for create/list/get/update/delete instance flows.
2. Write tests for owner isolation and disabled/deleted filtering.
3. Write tests for grant CRUD and audit append/query.
4. Implement `ConnectorRepository`.
5. Enforce active connector name uniqueness in repository logic.
6. Run:

```powershell
cd backend
uv run pytest tests/test_connectors_repository.py tests/test_connectors_user_isolation.py -q
```

---

## Task 4: Define Schemas And Registry

**Files:**

- Create: `backend/packages/harness/deerflow/connectors/schemas.py`
- Create: `backend/packages/harness/deerflow/connectors/registry.py`
- Create: `backend/packages/harness/deerflow/connectors/errors.py`
- Test: `backend/tests/test_connectors_registry.py`

**Steps:**

1. Write tests for built-in `mysql` and `starrocks` type registration.
2. Define Pydantic models for connector type, instance, credentials, policy, grant, metadata, query result, and runtime context.
3. Implement registry lookup and config validation.
4. Ensure type response masks adapter internals.
5. Run:

```powershell
cd backend
uv run pytest tests/test_connectors_registry.py -q
```

---

## Task 5: Add SecretStore

**Files:**

- Create: `backend/packages/harness/deerflow/connectors/secrets.py`
- Test: `backend/tests/test_connectors_secrets.py`

**Steps:**

1. Write tests for resolving env refs and missing-env failures.
2. Implement `SecretStore` protocol and `EnvSecretStore`.
3. Return structured secret values without logging them.
4. Add redaction helper for exceptions and logs.
5. Leave `EncryptedDbSecretStore` as interface placeholder unless v1 requires UI-entered secrets.
6. Run:

```powershell
cd backend
uv run pytest tests/test_connectors_secrets.py -q
```

---

## Task 6: Add Policy Engine

**Files:**

- Create: `backend/packages/harness/deerflow/connectors/policy.py`
- Test: `backend/tests/test_connectors_policy.py`

**Steps:**

1. Write tests for policy merge from system default, type default, instance policy, grant override, and runtime policy.
2. Verify allowlists take intersection, blocklists take union, `max_rows` and timeout take stricter values.
3. Verify read-only cannot be escalated.
4. Implement `authorize_connector_action()`.
5. Implement user/skill/agent/thread grant matching.
6. Run:

```powershell
cd backend
uv run pytest tests/test_connectors_policy.py -q
```

---

## Task 7: Add SQL Safety Validator

**Files:**

- Create: `backend/packages/harness/deerflow/connectors/sql_safety.py`
- Test: `backend/tests/test_connectors_sql_safety.py`

**Steps:**

1. Add dependency candidate to `backend/packages/harness/pyproject.toml`: `sqlglot`.
2. Write tests that allow simple `SELECT` and `WITH ... SELECT`.
3. Write tests that reject multi-statements, DML, DDL, admin commands, unsafe functions, unbounded query when `require_limit=true`, blocked tables, and tables outside allowlist.
4. Write separate tests for MySQL and StarRocks dialect settings.
5. Implement AST validation and table extraction.
6. Implement safe limit injection only when the parsed query is simple enough; otherwise require explicit `LIMIT`.
7. Run:

```powershell
cd backend
uv run pytest tests/test_connectors_sql_safety.py -q
```

---

## Task 8: Add Database Adapter Base

**Files:**

- Create: `backend/packages/harness/deerflow/connectors/adapters/base.py`
- Create: `backend/packages/harness/deerflow/connectors/adapters/database.py`
- Test: `backend/tests/test_mysql_connector_adapter.py`
- Test: `backend/tests/test_starrocks_connector_adapter.py`

**Steps:**

1. Add dependency candidate to `backend/packages/harness/pyproject.toml`: `asyncmy`.
2. Define `ConnectorAdapter` and `DatabaseConnectorAdapter`.
3. Implement shared database query execution contract.
4. Normalize results into serializable column and row structures.
5. Enforce max bytes, max field length, truncation flag, and elapsed time.
6. Add unit tests with fake engines/connections so no live database is required.
7. Run:

```powershell
cd backend
uv run pytest tests/test_mysql_connector_adapter.py tests/test_starrocks_connector_adapter.py -q
```

---

## Task 9: Add MySQL Adapter

**Files:**

- Create: `backend/packages/harness/deerflow/connectors/adapters/mysql.py`
- Test: `backend/tests/test_mysql_connector_adapter.py`

**Steps:**

1. Write tests for DSN building from env URL and split credential payload.
2. Write tests for connection test behavior.
3. Write tests for MySQL introspection SQL generation and result mapping.
4. Implement `MySQLConnectorAdapter`.
5. Ensure error messages are redacted.
6. Run:

```powershell
cd backend
uv run pytest tests/test_mysql_connector_adapter.py -q
```

---

## Task 10: Add StarRocks Adapter

**Files:**

- Create: `backend/packages/harness/deerflow/connectors/adapters/starrocks.py`
- Test: `backend/tests/test_starrocks_connector_adapter.py`

**Steps:**

1. Write tests for StarRocks query port default `9030`.
2. Write tests for StarRocks-specific type normalization.
3. Write tests for StarRocks introspection SQL generation and result mapping.
4. Implement `StarRocksConnectorAdapter` on top of the database adapter base.
5. Deny `SHOW`, `ADMIN`, `SET GLOBAL`, and mutation statements through SQL safety before adapter execution.
6. Run:

```powershell
cd backend
uv run pytest tests/test_starrocks_connector_adapter.py -q
```

---

## Task 11: Add Connector Service

**Files:**

- Create: `backend/packages/harness/deerflow/connectors/service.py`
- Create: `backend/packages/harness/deerflow/connectors/audit.py`
- Create: `backend/packages/harness/deerflow/connectors/resources.py`
- Test: `backend/tests/test_connectors_audit.py`
- Test: `backend/tests/test_connectors_service.py`

**Steps:**

1. Write tests for create/test/introspect/query service flows.
2. Write tests that deny disabled connectors and unauthorized subjects.
3. Write tests that audit both allow and deny decisions.
4. Implement service orchestration: repository, registry, secret store, policy engine, adapter, audit.
5. Implement metadata cache refresh after introspection.
6. Run:

```powershell
cd backend
uv run pytest tests/test_connectors_service.py tests/test_connectors_audit.py -q
```

---

## Task 12: Add FastAPI Router

**Files:**

- Create: `backend/app/gateway/routers/connectors.py`
- Modify: `backend/app/gateway/app.py`
- Test: `backend/tests/test_connectors_router.py`

**Steps:**

1. Write router tests with auth/user context helpers already used in gateway tests.
2. Implement all API endpoints listed above.
3. Mask secrets in all responses.
4. Convert service errors to stable HTTP status codes and error bodies.
5. Include router in `create_app()`.
6. Run:

```powershell
cd backend
uv run pytest tests/test_connectors_router.py -q
```

---

## Task 13: Add Runtime Tools

**Files:**

- Create: `backend/packages/harness/deerflow/connectors/tools.py`
- Modify: `backend/packages/harness/deerflow/tools/tools.py`
- Test: `backend/tests/test_connectors_tools.py`
- Test: `backend/tests/test_tool_deduplication.py`

**Steps:**

1. Write tests that connector tools are hidden when `connectors.enabled=false`.
2. Write tests that connector tools are loaded when enabled.
3. Write tests for safe summaries from `list_connectors`.
4. Write tests for `query_database` success and policy denial with fake service.
5. Implement LangChain tools with explicit argument schemas.
6. Make async tools sync-invocable using existing `make_sync_tool_wrapper` path when needed.
7. Run:

```powershell
cd backend
uv run pytest tests/test_connectors_tools.py tests/test_tool_deduplication.py -q
```

---

## Task 14: Integrate Runtime Context And Skill Requirements

**Files:**

- Modify: `backend/packages/harness/deerflow/skills/types.py`
- Modify: `backend/packages/harness/deerflow/skills/parser.py`
- Modify: `backend/packages/harness/deerflow/skills/validation.py`
- Modify: `backend/packages/harness/deerflow/agents/lead_agent/prompt.py`
- Test: `backend/tests/test_skills_parser.py`
- Test: `backend/tests/test_lead_agent_prompt.py`

**Steps:**

1. Extend skill frontmatter support for:

```yaml
requires:
  connectors:
    - capability: database.query
      purpose: analyze business metrics
```

2. Validate shape but do not require every skill to declare connectors.
3. Add safe connector summaries to lead agent prompt only when current context has granted connectors.
4. Keep summaries short to avoid prompt bloat.
5. Run:

```powershell
cd backend
uv run pytest tests/test_skills_parser.py tests/test_lead_agent_prompt.py -q
```

---

## Task 15: Add Documentation

**Files:**

- Modify: `docs/design/CONNECTOR_PLATFORM_DESIGN.md`
- Create: `backend/docs/CONNECTORS.md`
- Modify: `backend/docs/CONFIGURATION.md`
- Modify: `README_zh.md`

**Steps:**

1. Update first-version scope from Postgres/MySQL to MySQL/StarRocks.
2. Document config:

```yaml
connectors:
  enabled: true
  enabled_types: ["mysql", "starrocks"]
  secret_store:
    provider: env
  default_policy:
    database:
      mode: read_only
      max_rows: 10000
      statement_timeout_ms: 30000
```

3. Document env secret examples.
4. Document SQL safety limitations.
5. Document audit behavior and what is never logged.

---

## Task 16: Optional Frontend Management UI

**Files:**

- Create: `frontend/src/core/connectors/types.ts`
- Create: `frontend/src/core/connectors/api.ts`
- Create: `frontend/src/core/connectors/hooks.ts`
- Create: `frontend/src/app/workspace/connectors/page.tsx`
- Modify: workspace navigation component after locating the existing nav file.
- Test: `frontend/tests/unit/core/connectors/*.test.ts`

**Steps:**

1. Add connector list page with name, type, status, health, last used, grants, and actions.
2. Add create/edit flow for MySQL and StarRocks.
3. Use env ref first instead of raw password entry if v1 stays with `env` SecretStore.
4. Add schema viewer and audit tab.
5. Keep UI operational and compact, matching existing workspace management pages.
6. Run:

```powershell
cd frontend
pnpm test
```

---

## Suggested Milestones

### Milestone 1: Core Catalog And Governance

Tasks 1 through 6.

Outcome:

- Connector config loads.
- Connector tables exist.
- Registry knows `mysql` and `starrocks`.
- Repository and grants work.
- Secret references and policy merge work.

### Milestone 2: SQL Safety And Adapters

Tasks 7 through 10.

Outcome:

- SQL validator rejects unsafe queries.
- MySQL adapter can test, introspect, and query.
- StarRocks adapter can test, introspect, and query.
- Unit tests do not require live databases.

### Milestone 3: Service, API, And Audit

Tasks 11 and 12.

Outcome:

- Backend API can create, test, introspect, query, grant, disable, and audit connectors.
- Secrets are masked.
- Denials and errors are audited.

### Milestone 4: Agent Runtime

Tasks 13 and 14.

Outcome:

- Agent can discover authorized database connectors.
- Agent can query through `query_database`.
- Skill requirements can declare connector capability needs.
- No connector secrets enter prompt or tool results.

### Milestone 5: Docs And UI

Tasks 15 and 16.

Outcome:

- Operators can configure and use v1 from docs.
- Users can manage connectors in workspace UI if UI is included in first release.

---

## Acceptance Criteria

- User can create at least two MySQL connectors and two StarRocks connectors without restarting DeerFlow.
- `GET /api/connector-types` returns `mysql` and `starrocks`.
- `POST /api/connectors/{id}/test` works with env-backed credentials.
- `POST /api/connectors/{id}/introspect` caches schema/table/column metadata.
- `list_connectors` returns only authorized safe connector summaries.
- `query_database` executes read-only SQL and returns structured results.
- `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, multi-statement SQL, admin statements, and blocked-table access are rejected by code.
- Queries exceeding max rows, timeout, or result byte limits are truncated or rejected according to policy.
- Every connector runtime call writes an audit record.
- No API response, prompt injection, tool output, audit log, or application log contains a raw password, token, or full connection URL.
- Existing DeerFlow app database configuration continues to work unchanged.

---

## Verification Commands

Focused backend:

```powershell
cd backend
uv run pytest tests/test_connectors_config.py tests/test_connectors_models.py tests/test_connectors_repository.py tests/test_connectors_registry.py tests/test_connectors_policy.py tests/test_connectors_secrets.py tests/test_connectors_sql_safety.py tests/test_mysql_connector_adapter.py tests/test_starrocks_connector_adapter.py tests/test_connectors_service.py tests/test_connectors_router.py tests/test_connectors_tools.py tests/test_connectors_audit.py -q
```

Broader backend:

```powershell
cd backend
uv run pytest tests -q
```

Compile:

```powershell
cd backend
uv run python -m compileall app packages
```

Frontend, only if UI is included:

```powershell
cd frontend
pnpm test
```

---

## Open Decisions Before Implementation

1. Secret v1: only `env`, or also `encrypted_db` for UI-entered credentials?
2. UI v1: full connector management UI, or backend API first?
3. Audit SQL: store only hash and table summary, or also store redacted normalized SQL preview?
4. Tenant model: use `owner_id` only for v1, or require `tenant_id` on day one?
5. Live integration tests: should CI spin up MySQL and StarRocks containers, or keep live tests opt-in?
6. StarRocks timeout behavior: which deployment/version should be the reference for session timeout settings?

---

## Recommended First PR Breakdown

1. `feat(connectors): add connector config and persistence models`
2. `feat(connectors): add registry, secrets, policy, and repository`
3. `feat(connectors): add SQL safety validator`
4. `feat(connectors): add MySQL and StarRocks adapters`
5. `feat(connectors): add service and audit pipeline`
6. `feat(api): add connector management endpoints`
7. `feat(agent): expose connector runtime tools`
8. `docs(connectors): document MySQL and StarRocks connector MVP`
