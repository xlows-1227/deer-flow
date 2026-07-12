# Multi-Tenant Agent Publishing Design

**Status:** Approved design

**Date:** 2026-07-12

## 1. Summary

DeerFlow will evolve from a user-scoped custom-agent workspace into a multi-tenant publishing platform. Each authenticated platform user can create multiple agents, define their behavior through `AGENT.md` and `SOUL.md`, select platform-public and owner-private Skills, grant specific Connector capabilities, and publish an immutable internal release.

External consumers do not see releases or version numbers. They interact with a stable Agent identity through either:

- an Agent-specific API protected by Agent-specific API Keys; or
- an optional Feishu application and bot bound to that Agent after publication.

The design adds a control plane for authoring, publication, credentials, quotas, and operations while retaining the existing DeerFlow LangGraph runtime as the shared execution plane.

## 2. Existing Capabilities and Gaps

The repository already provides most execution primitives:

- user-scoped custom agents stored under `users/{user_id}/agents/{name}/`;
- `SOUL.md`, `config.yaml`, model overrides, tool-group allowlists, and Skill allowlists;
- public and custom Skill loading;
- user isolation, Connectors, LangGraph threads and runs, streaming, artifacts, and audit-related middleware;
- an External API with API Key, Conversation, and Run concepts;
- Feishu, Slack, Telegram, DingTalk, Discord, WeChat, and WeCom channel adapters.

The current product is not yet a publishing platform:

- custom Agent definitions are mutable filesystem state rather than immutable published snapshots;
- Agent metadata, publication state, credentials, and routing are not modeled as multi-tenant database entities;
- the IM `ChannelService` is a process-wide singleton loaded from `config.yaml`;
- internal channel calls currently use a synthetic default user rather than a published Agent owner principal;
- API Keys are user-oriented rather than bound to one stable published Agent;
- there is no draft/publish/rollback workflow or per-Agent operational surface.

The redesign fills these gaps without replacing the LangGraph runtime.

## 3. Product Decisions

The following decisions are fixed for the first release:

1. A platform user can own and publish multiple Agents.
2. Each Agent may define both files:
   - `AGENT.md` describes purpose, responsibilities, workflows, boundaries, and output requirements.
   - `SOUL.md` describes personality, values, tone, and interaction style.
3. At least one of `AGENT.md` or `SOUL.md` must be non-empty. When both exist, the runtime composes `AGENT.md` first and `SOUL.md` second in separately labeled prompt sections.
4. An Agent can select platform-public Skills and Skills privately owned by the Agent creator.
5. A private Skill remains private. Publishing an Agent that uses it does not publish the Skill source to a marketplace or expose it to external callers.
6. Connector calls use the creator's Connector credentials only after the creator grants specific capabilities to the Agent.
7. Drafts and online releases are separate. Saving a draft never changes online behavior.
8. Internal releases are immutable and available only in the creator control plane. External consumers never select, receive, or otherwise observe an Agent release.
9. A published Agent can exist without a Feishu binding and without an API Key. Integrations are added after publication.
10. The first IM integration delivered is Feishu. The architecture retains a generic channel adapter boundary for later channels.
11. A Feishu binding uses one independent Feishu application and bot for one Agent. Anyone able to message that bot or add it to a group may use it; the first release has no Feishu user, department, or group allowlist.
12. External conversations are isolated by caller or chat. The external runtime does not read, extract, or write long-term memory.
13. Each Agent may have multiple named API Keys. Keys can be created, rotated, revoked, and limited independently.
14. Usage is charged to the Agent owner.
15. Creator-configured quotas are optional in the first release. Unset values inherit deployment-wide platform defaults; platform hard limits can never be disabled by an owner.
16. The first release does not include an Agent marketplace or public Agent detail page.

## 4. Goals

- Let every authenticated user create and manage multiple independent Agents.
- Provide a direct web editor for Agent instructions and capability selection while retaining the existing conversational bootstrap as an optional authoring aid.
- Produce immutable, reproducible online releases.
- Expose one stable external Agent identity independent of internal publication history.
- Support Agent-specific API integration and optional independent Feishu bots.
- Preserve strict ownership, Skill, Connector, secret, conversation, and usage isolation.
- Reuse the existing LangGraph runtime, RunManager, streaming, artifact, Skill, Connector, and channel adapter capabilities.
- Make publication, rollback, channel restart, secret rotation, and Key revocation operationally safe.

## 5. Non-Goals

- A public Agent marketplace, discovery page, ranking, reviews, or monetization.
- External visibility or selection of internal Agent releases.
- Feishu user, department, or group allowlists in the first release.
- End-user OAuth or bring-your-own Connector credentials.
- Long-term memory for public/API/IM execution.
- Shipping every existing IM adapter in the first release.
- Deploying one runtime service per Agent.
- Publishing private Skill source code.

## 6. Architecture

### 6.1 Control Plane

The control plane is used by authenticated Agent owners and contains four bounded modules.

#### Agent Studio

- creates and edits Agent drafts;
- edits `AGENT.md` and `SOUL.md` directly;
- chooses the default model and permitted tool groups;
- selects public and owner-private Skills;
- grants Connector capabilities;
- provides draft sandbox chat and optional conversational authoring.

#### Publication Service

- validates a draft and all referenced capabilities;
- resolves each Skill to a concrete immutable Skill revision;
- creates an immutable `AgentRelease` snapshot;
- atomically moves the Agent's `current_release_id` pointer;
- supports owner-visible history, comparison, and rollback;
- never exposes release identifiers through external APIs or channels.

#### Integration Management

- creates, rotates, names, limits, and revokes Agent API Keys;
- creates, tests, starts, stops, and updates an optional Feishu binding;
- stores only references to encrypted secrets;
- reports integration health independently from publication health.

#### Operations

- shows run counts, token usage, errors, latency, quota consumption, and channel status;
- exposes audit records to the Agent owner and platform administrators;
- supports Agent suspension without deleting drafts, releases, bindings, or history.

### 6.2 Data and Secret Layer

- SQL stores ownership, mutable draft metadata, immutable release metadata, routing, quotas, usage, and audit records.
- Versioned content storage stores immutable instruction and Skill-revision snapshots. A local filesystem backend may be used initially, behind a storage interface that can later use object storage.
- Secret storage holds Feishu App Secrets and Connector secrets. Database rows store `secret_ref`, never plaintext.
- API Key plaintext is displayed only at creation. The database stores a prefix for identification and a slow, salted hash for verification.

### 6.3 Entry Plane

#### Feishu Bot Supervisor

The current global, configuration-file-driven `ChannelService` becomes a database-driven supervisor. It can start, stop, and restart one Feishu channel instance per active Agent binding without restarting the Gateway or affecting other Agent bindings.

The supervisor is responsible for:

- loading active bindings without returning secret values to callers;
- event signature verification and replay protection;
- event deduplication by Feishu event/message identifier;
- mapping a binding to exactly one stable `agent_id`;
- applying ingress rate limits before creating a run;
- delivering streaming or final responses and attachments;
- surfacing per-binding health and restart state.

#### Agent External API

The external API is an Agent-specific facade over the existing Conversation and Run services. The protocol path may include `/v1`; that is the HTTP protocol version, not an Agent release.

External callers can:

- inspect safe, stable Agent metadata;
- create an isolated Conversation;
- submit synchronous, SSE-streaming, or asynchronous runs;
- query or cancel asynchronous runs;
- list safe API capability metadata and usage for their own Key where appropriate.

External callers cannot:

- provide or override `owner_user_id`, internal `release_id`, model, Skill, tool group, Connector, runtime context, or memory settings;
- access draft content, instruction source, private Skill source, Connector configuration, secrets, internal paths, or publication history;
- use an API Key issued for one Agent to invoke another Agent.

### 6.4 Shared Execution Plane

A new `PublishedAgentResolver` sits before the existing DeerFlow runtime. For every external request it:

1. resolves the stable Agent and confirms that it is published and active;
2. reads the Agent's `current_release_id` internally;
3. resolves the immutable instruction, model, Skill revisions, tool-group policy, and Connector capability grants;
4. constructs a trusted `PublishedAgentContext`;
5. reserves quota and concurrency capacity;
6. invokes the existing RunManager/LangGraph runtime;
7. finalizes idempotent usage accounting and audit records.

The trusted context contains at least:

- `owner_user_id`;
- `agent_id`;
- internal `release_id`;
- source type and binding or API Key identifier;
- external actor and conversation subject;
- allowed Skill revision identifiers;
- allowed Connector capabilities;
- tool-group policy;
- effective platform and owner quota policy;
- correlation and idempotency identifiers;
- `memory_enabled=false`.

The runtime executes under the owner principal for authorized resource access while retaining the external actor as a separate audit identity. External actor identifiers must never be treated as platform owner identifiers.

## 7. Domain Model

### 7.1 `agents`

Stable product identity owned by one platform user.

Key fields:

- `id`;
- `owner_user_id`;
- owner-unique `slug`;
- display name, description, and avatar reference;
- lifecycle status: `draft`, `published`, `suspended`, or `archived`;
- nullable internal `current_release_id`;
- timestamps.

An Agent becomes externally runnable only when status is `published` and `current_release_id` is set.

### 7.2 `agent_drafts`

Mutable creator-only state.

Key fields:

- `agent_id`;
- `agent_markdown`;
- `soul_markdown`;
- selected model and tool groups;
- optional creator quota overrides;
- optimistic concurrency revision;
- updated timestamp and user.

Draft Skill selections and Connector grants may use normalized child tables so ownership and capability validation remain queryable.

### 7.3 `agent_releases`

Immutable creator-visible publication snapshot.

Key fields:

- internal `id`;
- `agent_id` and monotonic owner-visible `release_no`;
- complete `AGENT.md` and `SOUL.md` snapshots or immutable content references;
- resolved model and runtime policy;
- effective owner quota overrides at publication time;
- canonical manifest and checksum;
- creator and publication timestamp.

Rows are never updated after creation. Rollback changes `agents.current_release_id`; it does not mutate or recreate a historical release.

### 7.4 `skill_revisions` and `agent_release_skills`

Publication must lock every selected Skill to a concrete revision. Updating a public or private Skill after publication does not change an online Agent until the owner republishes.

The release manifest records:

- Skill identity and revision;
- ownership/visibility classification;
- content checksum;
- declared tools and Connector capability requirements;
- compatibility metadata required by the runtime.

### 7.5 `agent_release_connector_grants`

An immutable release-level allowlist of Connector capabilities. It references owner-controlled Connector instances without embedding secrets.

Effective permission is always the intersection of:

1. platform policy;
2. Skill-declared requirements;
3. owner grant captured by the current release;
4. current Connector status and credential validity.

Granting a broad Connector does not implicitly grant every capability. Revoking a Connector or capability takes effect immediately as a security override, even for an older immutable release.

### 7.6 `agent_channels`

Stable integration bound to an Agent, not a release.

First-release constraints:

- channel type is `feishu`;
- an Agent has zero or one active Feishu binding;
- fields include App ID, encrypted secret reference, connection mode, status, health, and timestamps;
- publication and rollback do not change the binding;
- a binding may be added, updated, disabled, or removed after publication.

### 7.7 `agent_api_keys`

Stable credentials bound to exactly one Agent, not a release.

Key fields:

- internal Key ID and `agent_id`;
- owner-defined name;
- non-secret prefix and secret hash;
- status and last-used timestamp;
- optional Key-specific quota overrides;
- creation, rotation, expiration, and revocation metadata.

One Agent may have multiple active Keys to isolate different integrating systems.

### 7.8 Conversations and Channel Mappings

External conversation continuity is thread-scoped state, not long-term memory.

- API Conversations are scoped to `agent_id` and the authenticated Key/integration subject.
- Feishu private chats map independently by binding, chat, and Feishu user.
- Feishu group chats map by binding, chat, and topic/thread when available; group members intentionally share that group conversation context.
- no mapping can be reused across Agents or bindings.

The mapping store moves from the current global JSON file to the configured persistence layer so it works across processes and replicas.

### 7.9 Usage, Reservations, and Audit

Usage records include:

- owner and Agent;
- source (`api` or `feishu`);
- API Key or channel binding;
- external subject and internal conversation/run identifiers;
- model, tokens, latency, outcome, and error classification;
- idempotency and correlation identifiers;
- timestamps.

Quota reservations and final accounting use unique request/event identifiers so retries do not produce duplicate runs or duplicate charges.

## 8. Authoring and Publication Flow

### 8.1 Create and Edit

1. The owner creates an Agent identity and draft.
2. The owner edits basic metadata, `AGENT.md`, and `SOUL.md`.
3. The owner selects public and owner-private Skills.
4. The UI displays required Connector capabilities and asks the owner to grant them explicitly.
5. Optional model, tool-group, and owner quota overrides can be configured.
6. Draft sandbox chat uses draft configuration and is visibly marked as not online.

The current conversational `setup_agent` workflow can remain as an authoring aid, but direct structured editing is the source of truth for the draft.

### 8.2 Validate and Publish

Publication rejects a draft unless:

- at least one instruction file is non-empty;
- instruction size and safety checks pass;
- the model is available to the owner;
- all selected Skills exist, are enabled, are public or owned by the creator, and can produce immutable revisions;
- declared Connector requirements are covered by explicit grants;
- referenced Connector instances still belong to the owner;
- tool groups and runtime settings are valid;
- optional owner limits do not exceed platform limits.

On success, the service creates an immutable release and atomically sets `current_release_id`. An Agent may be published with no Feishu binding and no API Key; it simply has no external entry point until the owner creates one.

### 8.3 Republish and Rollback

- Draft edits after publication do not affect the current release.
- Republish creates a new immutable release and atomically changes the pointer.
- Rollback atomically points to a selected prior release.
- Feishu bindings, API endpoints, API Keys, and existing Conversation identifiers remain stable.
- in-flight runs continue with the release resolved at run creation; new runs use the newly selected current release.
- external responses never expose the resolved release identifier or number.

### 8.4 Add Integrations After Publication

The post-publication integration area lets the owner independently:

- create an API Key and copy API examples;
- add and test a Feishu application;
- rotate or revoke a Key;
- rotate Feishu credentials;
- pause or restart a channel;
- add optional Agent or Key quota overrides.

Integration failure does not unpublish the Agent. It marks only that integration unhealthy or disabled.

## 9. External API Contract

The exact route names may follow the repository's existing External API conventions, but the contract must preserve the following semantics.

### 9.1 Authentication

- `Authorization: Bearer <agent-api-key>` authenticates one Key and resolves exactly one `agent_id`.
- If the route also contains `agent_id`, it must match the Key binding or return a non-enumerating not-found response.
- API Key rotation can overlap old and new Keys intentionally; revocation is immediate.
- Keys never grant access to Agent management APIs.

### 9.2 Conversations and Runs

The API supports:

- Conversation creation;
- message/run submission;
- synchronous wait;
- SSE streaming;
- asynchronous Run creation, status, result, and cancellation;
- idempotent creation with `Idempotency-Key`.

Callers may provide user messages, supported attachments, client metadata from a safe allowlist, and a Conversation identifier. They may not supply runtime policy or internal context.

### 9.3 Safe Responses

Responses can include:

- stable Agent ID and display metadata;
- Conversation and Run identifiers;
- status, messages, usage allowed by policy, artifacts, and errors;
- request/correlation identifiers.

Responses exclude:

- internal release identifiers or numbers;
- owner user ID;
- Agent instruction source;
- private Skill metadata or source;
- Connector configuration and secrets;
- filesystem paths and internal runtime configuration.

## 10. Feishu Execution Flow

1. The per-Agent Feishu adapter receives an event.
2. It verifies the signature/token and rejects invalid or replayed events.
3. It deduplicates the event before quota reservation.
4. The binding resolves one stable Agent.
5. The mapping service resolves or creates an isolated internal thread.
6. The resolver checks Agent publication status and effective ingress limits.
7. It resolves the current release and constructs `PublishedAgentContext`.
8. The shared runtime processes the request.
9. The adapter streams updates when supported and publishes the final response or attachments.
10. Usage and audit records are finalized exactly once.

Feishu users do not need DeerFlow platform accounts. The Agent is public to anyone who can interact with its bot.

## 11. No-Memory Runtime Policy

External execution may retain messages inside the selected Conversation/thread so multi-turn chat works. It must not use DeerFlow long-term memory features.

For API and Feishu sources, the runtime must:

- disable memory extraction, consolidation, queues, and writes;
- disable memory prompt injection;
- exclude owner `USER.md`, owner profile memory, per-Agent memory, and global memory;
- omit memory management tools;
- prevent external callers from overriding this policy;
- prevent `setup_agent`, `update_agent`, Skill-management, Connector-management, and other control-plane mutation tools from being exposed.

Published behavior is derived only from the current release, allowed Skills, authorized Connector capabilities, current Conversation messages, safe attachment context, and platform runtime policy.

## 12. Quotas and Abuse Protection

### 12.1 Mandatory Platform Limits

Deployment-wide defaults and hard caps are required for:

- maximum concurrent runs per Agent and per Gateway instance;
- maximum input and attachment sizes;
- maximum execution duration;
- maximum model tokens or cost per run;
- ingress request/event rate;
- queue size and overload shedding;
- repeated failure circuit breaking.

Owners cannot disable or exceed these caps.

### 12.2 Optional Owner Limits

Owners may optionally configure stricter limits for:

- daily runs;
- daily tokens or budget;
- Agent concurrency;
- per-run timeout or token limit;
- individual API Keys.

An omitted value inherits the effective platform default. It never means unlimited.

### 12.3 Enforcement

- Limits are checked and reserved before a runtime Run is created.
- An exceeded limit returns API `429` with `Retry-After` where meaningful and a friendly Feishu message.
- Rejected requests do not create Runs or consume model quota.
- Reservations are finalized or released on success, cancellation, timeout, and failure.
- Duplicate Feishu events and duplicate idempotent API requests reuse the original outcome and are not charged twice.

## 13. Security

### 13.1 Principal Separation

Every run carries two distinct identities:

- owner principal: authorizes the Agent, Skill, model, and Connector resources;
- external actor principal: identifies the API integration or Feishu subject for conversation isolation, limits, and audit.

No inbound field may set either trusted identity directly.

### 13.2 Least-Privilege Capability Resolution

Runtime tools are derived from allowlist intersections. The resolver does not pass raw secret material into prompts, Skill files, logs, or model-visible metadata. Connector tools exchange opaque connection identifiers for authorized execution inside the Connector layer.

### 13.3 Public-Agent Restrictions

The public runtime denies:

- control-plane mutations;
- Agent self-modification;
- Skill installation, editing, enablement, or deletion;
- Connector creation, credential changes, or permission changes;
- memory access or mutation;
- cross-Agent and cross-owner resource access;
- caller-provided internal context and hidden configuration fields.

Normal sandbox and file tools are available only when the release tool policy permits them and remain inside the thread's isolated workspace.

### 13.4 Secret Handling

- Feishu App Secrets and Connector credentials are encrypted and referenced by opaque IDs.
- Secret values are redacted from structured logs, traces, errors, and audit payloads.
- API Key plaintext cannot be recovered after creation.
- credential validation returns safe status without echoing secrets.

## 14. Error Handling

| Condition | API behavior | Feishu behavior | Operational behavior |
|---|---|---|---|
| Agent not published | `404` | generic unavailable message | no Run created |
| Agent suspended or archived | `410` or policy-equivalent not-found | paused-service message | audit denial |
| Invalid/revoked API Key | `401` | not applicable | rate-limited auth audit |
| Quota or concurrency exceeded | `429` | friendly busy/quota message | no Run created |
| Runtime timeout | timeout status with request ID | retry-later message | release reservation; record timeout |
| Connector authorization missing | safe capability-denied error | safe task failure | audit denied capability |
| Connector credential expired | safe dependency error | creator-contact message | mark Connector unhealthy; notify owner |
| Feishu binding invalid | not applicable | channel cannot start | Agent remains published; binding unhealthy |
| New release causes errors | stable external error | stable external error | owner can atomically roll back |

Errors exposed to external consumers never contain owner IDs, release IDs, internal paths, stack traces, prompts, secrets, or private capability details.

## 15. Observability and Operations

Metrics and structured logs use stable operational identifiers:

- `agent_id`;
- internal `release_id` in trusted telemetry only;
- channel binding or API Key ID;
- source and external subject hash;
- Conversation and Run ID;
- request/event idempotency key;
- correlation ID;
- latency, tokens, status, and error class.

Dashboards should cover:

- active Agents and bindings;
- runs, tokens, and cost by owner/Agent/source;
- quota rejection and concurrency saturation;
- Feishu connection health and event lag;
- Connector failures and authorization denials;
- error rates by current release so owners can identify regressions.

Owner views must not expose raw external user content by default. Platform administrators require an explicit support/audit path for sensitive payload access.

## 16. Web Experience

### 16.1 Agent Gallery

The existing gallery becomes the owner dashboard. Each card shows stable Agent status, current release summary, active integrations, recent usage, and health. It does not expose a public marketplace action.

### 16.2 Agent Studio

The Studio has focused sections rather than requiring channel setup during creation:

1. overview and basic metadata;
2. `AGENT.md` and `SOUL.md` editors;
3. public/private Skill selection and required Connector grants;
4. draft sandbox test;
5. publication validation, change summary, release history, and rollback.

After publication, an Integration and Operations area provides:

- API Keys and API examples;
- optional Feishu binding and connection test;
- optional Agent/Key quota overrides;
- health, usage, audit, pause, and restart controls.

### 16.3 Authoring Compatibility

The existing chat-driven Agent creation flow may generate or refine draft content, but it must write through the same draft service and authorization rules as the structured editor. It must not create filesystem-only Agents that bypass publication records.

## 17. Migration and Compatibility

Existing per-user filesystem Agents are not silently published.

Migration behavior:

1. list existing per-user Agent directories as import candidates;
2. create stable `agents` and `agent_drafts` records owned by the same user;
3. map existing `SOUL.md` and config fields into the draft;
4. leave `AGENT.md` empty unless one exists;
5. map current Skill names to visible current revisions, reporting unresolved Skills;
6. require owner review and explicit first publication;
7. preserve legacy runtime compatibility during a bounded migration window.

The existing External API remains available during migration. Agent-specific Keys and stable published-Agent routes are additive and must not reinterpret legacy user API Keys.

## 18. Testing Strategy

### 18.1 Unit Tests

- draft and release validation;
- instruction composition order;
- immutable release and checksum behavior;
- Skill revision and ownership resolution;
- Connector capability intersections;
- stable Agent-to-current-release resolution;
- API Key hashing, rotation, revocation, and Agent binding;
- quota inheritance, reservations, release, and idempotency;
- no-memory policy construction;
- safe external serialization and error redaction.

### 18.2 Repository and Service Tests

- owner-scoped CRUD for every new entity;
- cross-owner and cross-Agent denial;
- atomic publication and rollback pointer updates;
- immediate security revocation of Connector grants;
- stable bindings and Keys across republish/rollback;
- multi-process-safe Conversation and Feishu mapping persistence.

### 18.3 Runtime Integration Tests

- published instructions and exact Skill revisions reach the runtime;
- external fields cannot override model, Skill, Connector, owner, release, or memory policy;
- owner Connector access succeeds only for granted capabilities;
- memory middleware, memory prompts, `USER.md`, and management tools are absent;
- in-flight runs remain on their resolved release while new runs use the new release;
- usage is finalized exactly once on success, failure, cancellation, and timeout.

### 18.4 External API Tests

- multiple Keys for one Agent and strict rejection across Agents;
- synchronous, streaming, asynchronous, cancellation, and idempotent flows;
- multi-turn Conversation isolation;
- no internal release fields or private metadata in responses;
- correct `401`, `404/410`, `409`, `422`, `429`, and timeout behavior;
- no duplicate run or charge on retry.

### 18.5 Feishu Tests

- multiple independent Agent bindings in one Gateway deployment;
- connection start, stop, restart, and credential rotation;
- event verification, replay rejection, and deduplication;
- private-user, group, and topic Conversation mapping isolation;
- streaming/final delivery and attachment handling;
- no long-term memory across Conversations;
- one unhealthy binding does not affect another.

### 18.6 Frontend Tests

- direct editing of both instruction files;
- public/private Skill ownership filtering;
- Connector requirement and grant UX;
- publish validation and draft/online separation;
- release comparison and rollback without exposing releases externally;
- publication without Feishu or API Key;
- post-publication API Key and Feishu setup;
- optional limits inheriting platform defaults.

## 19. Acceptance Criteria

The first release is accepted when:

1. Two platform users can each create multiple Agents without reading or mutating the other's drafts, releases, Skills, Connectors, Keys, channels, usage, or audit data.
2. An Agent can publish with only `AGENT.md`, only `SOUL.md`, or both.
3. Saving a published Agent's draft does not change online behavior until republish.
4. Public and owner-private Skills are selectable, and online execution is locked to the published Skill revisions.
5. Connector calls use only owner-granted capabilities and never expose secrets.
6. An Agent can publish without Feishu and later serve API requests after an Agent Key is created.
7. An Agent can later bind an independent Feishu application without republishing.
8. Feishu private and group Conversations are isolated correctly and no external run reads or writes long-term memory.
9. Republish and rollback do not change the bot identity, external API path, API Keys, or existing Conversation identifiers.
10. External callers cannot observe or select internal releases.
11. Multiple named Agent Keys can be independently limited, rotated, and revoked.
12. Optional owner limits and mandatory platform limits reject work before Run creation and account idempotently.
13. Feishu event retries and API idempotency retries do not duplicate Runs or charges.
14. One failed Feishu binding, Connector, or Agent does not interrupt other published Agents.

## 20. Delivery Decomposition

This redesign spans several bounded subsystems and should be implemented as four sequential milestones, each with its own implementation plan and review gate.

### Milestone 1: Agent Control Plane and Releases

- SQL entities and repositories;
- draft service and direct editor APIs;
- Skill revisions and publication validation;
- immutable releases, atomic publish, history, and rollback;
- migration/import path for existing custom Agents.

### Milestone 2: Published Runtime and Agent API

- `PublishedAgentResolver` and trusted context;
- no-memory runtime profile and management-tool filtering;
- Agent API Keys and stable external API facade;
- quota reservation, usage, audit, sync/SSE/async runs;
- cross-owner security and idempotency tests.

### Milestone 3: Multi-Agent Feishu Supervisor

- database-backed channel bindings and secret references;
- per-Agent Feishu lifecycle and health;
- durable Conversation mapping and event deduplication;
- quota-aware runtime routing and response delivery;
- multi-binding isolation tests.

### Milestone 4: Agent Studio and Operations

- draft editors, Skill selection, Connector grant UX, and sandbox test;
- publish validation, history, comparison, and rollback UI;
- post-publication API Key and optional Feishu setup;
- optional limit controls, usage, health, and audit views;
- end-to-end acceptance coverage and operational documentation.

The milestones preserve usable intermediate states and avoid combining persistence, runtime security, channel orchestration, and UI changes into one unreviewable implementation batch.
