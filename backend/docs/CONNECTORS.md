# Connector Platform

DeerFlow connectors are managed external resources. A connector stores metadata, policy, grant, health, cached resources, and a secret reference. It does not expose raw credentials to prompts, tools, audit logs, or API responses.

## Version 1 Scope

Supported connector types:

- `mysql` / `starrocks` — read-only database analysis
- `onedata` — OneData agent APIs (`list_apis` / `get_params` / `call_api`)

Only connector types listed in `connectors.enabled_types` can be discovered, created, enabled, tested, or used at runtime.

The platform shape is intentionally generic. Database logic lives in database adapters; registry, secret resolution, grants, policy, audit, API, and runtime tools are connector-type neutral so later document, HTTP/OpenAPI, object storage, or BI connectors can reuse the same control plane.

## Configuration

```yaml
connectors:
  enabled: true
  enabled_types:
    - mysql
    - starrocks
    - onedata
  secret_store:
    provider: env
  default_policy:
    database:
      mode: read_only
      max_rows: 10000
      statement_timeout_ms: 30000
      require_limit: true
```

With `provider: env`, create connector instances with `credential.ref` pointing to an environment variable name. Example:

```env
PROD_ORDERS_MYSQL_URL=mysql+asyncmy://readonly:password@mysql.internal:3306/orders
ADS_STARROCKS_URL=mysql+asyncmy://readonly:password@starrocks-fe.internal:9030/ads
```

> [!IMPORTANT]
> If you create database connectors with inline username/password credentials, set `DEERFLOW_CONNECTOR_KEY` in the backend environment before creating connectors. This Fernet key encrypts inline connector passwords at rest. Without it, DeerFlow falls back to a development-only fixed key, which is not safe for production.
>
> Generate a key:
>
> ```bash
> python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
> ```
>
> Set it in `.env` or the backend runtime environment:
>
> ```env
> DEERFLOW_CONNECTOR_KEY=your-generated-fernet-key
> ```
>
> Keep this key out of source control and shared logs. If you change it after creating inline connectors, existing encrypted passwords cannot be decrypted with the new key; re-enter the connector passwords or run a credential migration. Connectors that use `credential.ref` to point at environment variables do not store inline passwords, so `DEERFLOW_CONNECTOR_KEY` is specifically required for inline credential storage.


## OneData

Configure the discovery base URL in the backend environment:

```env
ONEDATA_API_BASE_URL=http://share-onedata-api-ent4.prd.yumc.local/v1
```

Create an `onedata` connector with inline `secretId` / `secretKey` (stored as credential username/password). Two connector options control deployed-version compatibility:

- `auth_mode`: `legacy_raw` (default, sends the raw `secretKey` as `sign`) or `hmac_sha256` (builds the sorted OneData canonical string and sends its Base64 HMAC-SHA256 signature).
- `response_mode`: `raw` (default, returns HTTP-200 business payloads unchanged) or `strict` (turns non-success `status` values into recoverable connector errors). Strict mode accepts `-9999800`, `0`, and `200` as success values.

Both options are available in the connector settings form. Existing connectors with an empty `config` keep the legacy defaults. Runtime:

1. `call_connector_action` + `onedata.list_apis`
2. `get_onedata_api_params` with the returned numeric `api_id`
3. `call_onedata_api` with `api_id` and `param_data`; the adapter resolves the URL automatically

The generic `call_connector_action` remains compatible with `onedata.get_params` and `onedata.call_api`. It accepts `apiId` / `api_id`, `callUrl` / `calUrl` / `call_url`, `paramData` / `param_data`, and camelCase or snake_case paging fields. It normalizes deployed `callUrl` / legacy `calUrl` discovery fields and can resolve the business URL automatically when an API id is supplied. `pageSize` and `pageNum` must be supplied together.

Discovery responses are treated as successful when their JSON `code` is either the legacy OneData code `-9999800` or the HTTP-style code `200`. In both cases, `list_apis` returns the response `result` as `apis` instead of treating a successful HTTP response as a connector health error.

Business calls POST directly to the resolved business URL. Field encryption/decryption is not implemented.

### Local mock (downstream unavailable)

When the real OneData discovery/business APIs are not reachable, start a local mock from `backend/`:

```bash
# Local `make dev` (Gateway on host):
PYTHONPATH=. uv run python scripts/mock_onedata_server.py

# Docker Compose Gateway (container must reach host mock):
PYTHONPATH=. uv run python scripts/mock_onedata_server.py \
  --public-base http://host.docker.internal:18087
```

Then set in `.env` (and restart Gateway so it reloads env):

```env
# Local Gateway:
# ONEDATA_API_BASE_URL=http://127.0.0.1:18087/v1

# Docker Compose Gateway:
ONEDATA_API_BASE_URL=http://host.docker.internal:18087/v1
```

Use connector credentials `secretId=mock-secret-id` and `secretKey=mock-secret-key`. The mock exposes two sample APIs (`1001` sales, `1002` store info); `get_params` returns `calUrl` pointing back at the same process. `--public-base` must match the URL Gateway can reach, otherwise business `call_api` will fail the same way as discovery.

## API

Connector type discovery:

```http
GET /api/connector-types
GET /api/connector-types/mysql
GET /api/connector-types/starrocks
```

Connector management:

```http
POST /api/connectors
GET /api/connectors
GET /api/connectors/{connector_id}
PATCH /api/connectors/{connector_id}
DELETE /api/connectors/{connector_id}
POST /api/connectors/{connector_id}/enable
POST /api/connectors/{connector_id}/disable
```

Management responses omit the `credential` field. The API accepts a credential reference during create/update, stores the reference, and never echoes env var names, tokens, passwords, or connection URLs back to the caller.

Runtime operations:

```http
POST /api/connectors/{connector_id}/test
POST /api/connectors/{connector_id}/introspect
GET /api/connectors/{connector_id}/schema
POST /api/connectors/{connector_id}/query
POST /api/connectors/{connector_id}/sample
```

Grants and audit:

```http
POST /api/connectors/{connector_id}/grants
GET /api/connectors/{connector_id}/grants
PATCH /api/connectors/{connector_id}/grants/{grant_id}
DELETE /api/connectors/{connector_id}/grants/{grant_id}
GET /api/connectors/{connector_id}/audit
GET /api/connector-audit
```

## Runtime Tools

When `connectors.enabled` is true, DeerFlow exposes:

- `list_connectors`
- `inspect_connector`
- `query_database`
- `sample_database_table`
- `call_connector_action`

These tools return only safe summaries and structured results. They never return host details, usernames, passwords, tokens, or full connection URLs.

`call_connector_action` is the generic extension point for future connector categories. It still goes through connector grants, policy merge, secret resolution, adapter execution, and audit logging. Database query/sample/inspect capabilities are automatically routed back through the dedicated database safety paths.

## SQL Safety

Database connectors are read-only. The tool layer rejects:

- multi-statement SQL
- `INSERT`, `UPDATE`, `DELETE`, `MERGE`
- `DROP`, `ALTER`, `CREATE`, `TRUNCATE`
- `SHOW`, `DESCRIBE`, `EXPLAIN`, `SET`, `USE`, admin commands
- blocked table access
- schema/table access outside policy allowlists

Queries are bounded by `max_rows`, `statement_timeout_ms`, and result truncation rules.

Before validation/execution, SQL comments are stripped (`--`, `#`, `/* ... */`) while preserving comment-like text inside quoted string/identifier literals. This avoids syntax failures when models emit annotated SQL that some engines reject.

## Skill Requirements

Skills can declare connector capability requirements without naming a concrete connector or secret:

```yaml
requires:
  connectors:
    - capability: database.query
      purpose: Analyze order, revenue, and fulfillment metrics
```

At runtime, the agent still discovers concrete connectors through `list_connectors` based on the current user, skill, agent, and thread grants.

## Audit Behavior

Connector calls write audit records with connector id, type, capability, operation, user/thread/run context, decision, elapsed time, row counts, SQL hash, SQL preview, and referenced tables.

Audit records do not store passwords, tokens, full connection URLs, or result rows.
