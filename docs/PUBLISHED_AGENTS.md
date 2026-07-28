# Published Agents Operations Handbook

This handbook covers deployment, encrypted Feishu credentials, quota tuning,
incident diagnosis, and Release rollback for the multi-tenant Published-Agent
platform.

For application developers integrating through the Agent API, see the Chinese
[Published Agent API external integration guide](reference/PUBLISHED_AGENT_API_zh.md).

## 1. Runtime invariants

Operators should preserve these invariants during every deployment:

- `agent_id` is the stable public identity. External routes, Agent API Keys,
  Feishu bindings, and Conversation mappings attach to it, not to a Release.
- A Release is immutable. Publishing creates a new Release and atomically moves
  `current_release_id`; rollback only moves that pointer.
- Saving a draft never changes an already published runtime.
- External Runs are memory-free and cannot select an internal Release, model,
  Skill revision, Connector grant, owner, or runtime configuration.
- Platform limits are hard caps. Owner and Key overrides may only tighten them.
- SQL stores only Feishu `secret_ref` values. Plaintext credentials must never
  appear in database dumps, application logs, audit responses, or support
  tickets.

## 2. Deployment configuration

### 2.1 Persistent database

Published Agents require application persistence. SQLite is suitable for a
single-node deployment; PostgreSQL is the production choice when the rest of
the application requires multi-node database access.

```yaml
# config.yaml — single node
database:
  backend: sqlite
  sqlite_dir: .deer-flow/data
```

```yaml
# config.yaml — PostgreSQL
database:
  backend: postgres
  postgres_url: $DATABASE_URL
```

For PostgreSQL, install the `postgres` backend extra and inject
`DATABASE_URL` through the deployment secret manager. Gateway startup applies
the Alembic migration chain automatically. Back up the database before an
upgrade and verify that startup reaches the current migration head before
accepting authoring or external traffic.

`database.backend: memory` does not provide a durable Published-Agent control
plane and must not be used for production publishing.

### 2.2 Platform quota

The complete hard-cap configuration is:

```yaml
publishing:
  platform_quota:
    max_concurrent_runs_per_agent: 8
    max_input_bytes: 262144
    max_run_seconds: 600
    max_tokens_per_run: 200000
    inbound_rps: 20
    daily_runs_default: 1000
    daily_tokens_default: 2000000
  model_costs:
    model-name-from-config:
      input_usd_per_million_tokens: 2.50
      output_usd_per_million_tokens: 10.00
```

All values must be positive integers. A platform quota change requires a
Gateway restart. Existing immutable Releases retain their owner overrides, but
the resolver applies the current platform hard cap to every new Run.

`model_costs` is optional and keyed by the configured model name. The
operations view estimates terminal Run cost from these deployment rates. An
unconfigured model reports zero cost rather than inventing a price. Pricing
changes affect dashboard aggregation from the next Gateway restart.

### 2.3 Required secrets and process topology

Published Feishu bindings require a stable Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Store the result in the deployment secret manager as
`DEER_FLOW_SECRET_STORE_KEY`. Do not commit it to `.env`, `config.yaml`, or a
container image.

The first version permits exactly one Gateway process to own the Published
Feishu Supervisor. It acquires:

```text
${DEER_FLOW_HOME:-.deer-flow}/published-feishu-supervisor.lock
```

Stop the old Supervisor process cleanly before starting its replacement.
Do not run multiple independent Gateway replicas with Published Feishu enabled
until distributed Supervisor leasing is implemented.

Agent Key rotation overlap defaults to 24 hours and can be changed with:

```text
AGENT_API_KEY_ROTATION_OVERLAP_SECONDS
```

Use `0` only for emergency rotation where immediate predecessor expiry is
intentional.

### 2.4 Deployment verification

After startup:

1. Open the owner Gallery and confirm each tenant sees only its own Agents.
2. Open one published Agent and confirm Release history, quota policy, usage,
   operations metrics, and rejection audit load.
3. Create a short-lived Agent Key and store its plaintext immediately.
4. Create a test Conversation through the stable public API.
5. If Feishu is enabled, run **Test connection**, then **Start**, and wait for
   `status=active` with `health=healthy`.
6. Revoke the short-lived test Key.

Public API smoke test:

```bash
export DEER_FLOW_URL="https://deerflow.example.com"
export AGENT_ID="pa_..."
export AGENT_API_KEY="dfa_..."

curl --request POST \
  "$DEER_FLOW_URL/api/v1/agents/$AGENT_ID/conversations" \
  --header "Authorization: Bearer $AGENT_API_KEY" \
  --header "Content-Type: application/json" \
  --data '{"external_conversation_id":"deployment-smoke"}'

export CONVERSATION_ID="conversation-id-from-the-response"

curl --request POST \
  "$DEER_FLOW_URL/api/v1/agents/$AGENT_ID/conversations/$CONVERSATION_ID/runs/wait" \
  --header "Authorization: Bearer $AGENT_API_KEY" \
  --header "Content-Type: application/json" \
  --data '{"message":"Reply with deployment-ok"}'
```

The response must not contain `release_id`, `model_name`, internal instructions,
Skill revision IDs, Connector details, credential IDs, or thread IDs.

## 3. SecretStore operations

### 3.1 Storage layout

The local encrypted store lives under:

```text
${DEER_FLOW_HOME:-.deer-flow}/secret-store/feishu/
```

Each SQL binding row contains an opaque reference such as
`secret://feishu/<random-id>`. Ciphertext files are sharded below the directory.
`.pending/` contains non-secret ownership metadata used to recover a crash
between ciphertext creation and database transfer.

Back up the following as one consistency group:

- the application database;
- the complete Feishu SecretStore directory;
- the deployment reference to `DEER_FLOW_SECRET_STORE_KEY`.

The key itself should remain in the external secret manager, not in the backup
archive.

### 3.2 Master-key safety

The current release does not provide an in-place master-key re-encryption
command. Changing `DEER_FLOW_SECRET_STORE_KEY` without re-encrypting every
existing ciphertext makes all existing Feishu bindings unreadable.

If master-key rotation is required:

1. Schedule a maintenance window and stop the Gateway.
2. Take a consistent database and ciphertext backup.
3. Keep the old key available for rollback.
4. Use an audited re-encryption procedure or recreate every binding credential
   before retiring the old key.
5. Start the Gateway with the new key and test every binding.
6. Retire the old key only after all bindings are healthy and the rollback
   window has closed.

If the key is lost, SQL `secret_ref` values cannot recover the plaintext.
Provision new Feishu credentials and replace each affected binding.

### 3.3 Credential rotation

Use the Studio **Rotate credentials** action. It writes a new encrypted bundle,
advances the runtime generation, stops the old transport, and restarts the
binding when appropriate. Never edit `secret_ref` or ciphertext files manually.

After rotation:

- confirm the binding health returns to `healthy`;
- send one private-chat and one group-chat smoke message;
- confirm no secret value appears in logs or the owner audit view;
- retain the old application credential only for the organization's approved
  rollback window.

## 4. Quota tuning

### 4.1 Inheritance

Quota resolution is a monotonic minimum:

```text
platform hard cap → Release owner override → Agent Key override
```

A blank owner or Key field means “inherit the previous bounded value.” It never
means unlimited. The seven editable fields are:

| Field | Meaning |
| --- | --- |
| `max_concurrent_runs` | Simultaneous Runs for the Agent or credential |
| `daily_runs` | UTC-day Run admissions |
| `daily_tokens` | UTC-day settled token budget |
| `max_run_seconds` | Per-Run wall-clock limit |
| `max_tokens_per_run` | Per-Run model-token ceiling |
| `max_input_bytes` | Maximum admitted text plus attachment bytes |
| `inbound_rps` | One-second inbound request rate |

`max_tokens_per_run` is additionally bounded by the effective
`daily_tokens` value.

Owner quota edits are draft state and take effect only after publishing the
next Release. Key quota edits apply to subsequent requests without republishing.

### 4.2 Tuning procedure

1. Review 7-, 30-, and 90-day usage in Studio, split by API/Feishu and Key.
2. Check recent metadata-only rejection events:
   - `429` indicates quota admission;
   - `401` indicates authentication;
   - `403` indicates capability policy.
3. Decide whether the limit is a platform safety boundary, an Agent-wide
   product limit, or a customer/Key-specific limit.
4. Raise only the narrowest appropriate layer. Lower limits gradually.
5. For owner changes, save the draft, review the Release diff, and publish.
6. Repeat the smoke test and watch error rate, latency, concurrent reservations,
   and token settlement.

Quota rejection occurs before Run creation. Retrying the same idempotent API
request or Feishu event cannot create a duplicate Run or duplicate usage row.

### 4.3 Operations metrics

The owner dashboard is a metadata-only operational read model. It includes:

- Run, Token, configured cost, and terminal status by UTC day;
- quota rejection and `max_concurrent_runs` saturation counts;
- active/unhealthy Feishu binding counts and Feishu event-ingress-to-Run-dispatch
  average/P95 latency (model/Run execution time is excluded);
- Connector execution failures and authorization denials;
- Run/error counts and error rate for the Agent's current Release.

Cost is calculated and displayed as integer micro-USD at the API boundary to
avoid floating-point currency drift. Release IDs are used only inside the trusted
owner operations response; external Agent API responses still never expose
them. The dashboard does not expose messages, prompts, model output, actor
identities, IP hashes, credentials, or Connector request payloads.

## 5. Draft sandbox

Studio's Sandbox tab calls:

```text
POST /api/published-agents/{agent_id}/draft/sandbox-runs
```

The endpoint freezes the saved database draft revision before creating the Run.
It does not resolve the current Release, create a Published quota reservation,
or write a Published usage record, and its response is marked `billable=false`.
Use it to verify unpublished instructions, model selection, Skills, and
Connector selection. Saving or running a draft never changes the live
`current_release_id`.

Opening the sandbox conversation loads its owner-only scope from:

```text
GET /api/published-agents/draft/sandbox-threads/{thread_id}
```

The Thread also retains the Agent slug and display name so chat history can
route it back to the Agent chat and show an icon/name badge beside its title.
The composer displays only the frozen Skill names and Connector instances.
Every follow-up Run restores the same server-owned capability map; client
Thread metadata and Run bodies cannot widen it. If the saved draft revision
changes, the old conversation fails closed with
`draft_sandbox_revision_stale` (HTTP 409). Start a new sandbox Run to test the
new revision.

## 6. Feishu binding troubleshooting

Agent publish state and binding health are deliberately separate. A broken
Feishu application does not unpublish the Agent or interrupt its API Keys.

Use this sequence:

1. Confirm the Agent is published and not suspended or archived.
2. Confirm `DEER_FLOW_SECRET_STORE_KEY` is present and unchanged.
3. Open **Integrations**, run **Test connection**, and record only the redacted
   health detail.
4. Confirm the Feishu application has the expected App ID, event permissions,
   verification token, and WebSocket/event-delivery configuration.
5. If credentials changed, use **Rotate credentials**.
6. Run **Start** or **Restart** and wait for `healthy`.
7. Send a new event with a new Feishu event ID; repeated delivery of the same
   event is expected to be deduplicated.

| Symptom | Interpretation and action |
| --- | --- |
| Binding routes return `503` | SecretStore key is missing/invalid or the Supervisor did not start. Fix configuration and restart the single owner process. |
| `status=inactive`, `health=unknown` | The binding exists but is not requested to run. Test it, then start it. |
| `status=active`, `health=starting` | Startup is still converging. Wait through the normal startup deadline before restarting. |
| `health=unhealthy` | Use the redacted detail to correct credentials, permissions, or transport configuration, then test and restart. |
| `409` during start/restart/delete | A fenced lifecycle or cleanup operation is still converging. Do not create a duplicate binding; retry after the existing owner finishes. |
| Private users share a Conversation | Treat as a mapping invariant incident. Stop the binding and preserve database evidence. Private chats must be isolated by user. |
| Group replies split unexpectedly | Check whether Feishu topic IDs differ. Group topics intentionally map to separate Conversations. |
| One binding fails while peers stay healthy | Expected failure isolation. Diagnose only the failed binding; do not restart all Agents. |

Never paste `app_secret`, `verification_token`, `encrypt_key`, Agent Key
plaintext, raw user content, IP hashes, or actor hashes into a ticket. Use
Agent ID, binding ID, request/correlation ID, status, health, action, and
timestamp.

## 7. Release rollback SOP

Use rollback for a bad instruction, model, Skill snapshot, tool group, quota
override, or Connector grant introduced by a Release.

1. Suspend the Agent only if new traffic must stop immediately. Suspending is a
   lifecycle action and does not delete Releases, Keys, bindings, or mappings.
2. In Studio **Publish**, compare the current Release with the target historical
   Release.
3. Confirm the target has the intended instructions, pinned Skill revisions,
   model, tool groups, Connector grants, and owner quota.
4. Select **Roll back**, review the stable-identity warning, and confirm.
5. Verify `current_release_id` now points to the selected historical Release.
6. Run an API smoke request and, if configured, a Feishu private/group smoke
   request.
7. Resume the Agent if it was suspended.

Rollback preserves:

- Agent ID, slug, and `/api/v1/agents/{agent_id}` route;
- existing Agent API Keys and their quota overrides;
- Feishu binding identity and encrypted credentials;
- existing API and Feishu Conversation mappings;
- all historical Releases and the current mutable draft.

Do not revoke Keys or recreate Feishu bindings solely because of a Release
rollback. If the bad Release exposed a credential or otherwise caused a
security incident, rotate/revoke the affected credential as a separate
incident action.

Rollback does not rewrite the draft. After service is restored, correct the
draft and publish a new Release; do not try to mutate or delete the historical
bad Release.

## 8. Acceptance and regression

The final acceptance gate maps directly to design §19:

```bash
cd backend
uv run pytest tests/test_acceptance_multi_tenant.py -q

cd ../frontend
pnpm exec playwright test tests/e2e/multi-tenant-acceptance.spec.ts
```

Before production rollout, also run:

```bash
cd backend
make test

cd ../frontend
pnpm check
pnpm test
pnpm test:e2e
```

The acceptance suite covers tenant isolation, instruction combinations, draft
isolation, Skill revision pinning, least-privilege Connector grants, late Key
and Feishu setup, Conversation isolation, stable identity across publish and
rollback, Release non-disclosure, multi-Key lifecycle, pre-Run quota rejection,
API/Feishu idempotency, and cross-Agent failure isolation.
