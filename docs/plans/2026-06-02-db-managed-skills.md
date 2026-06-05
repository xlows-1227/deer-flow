# DB Managed Skills Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make DeerFlow skill metadata, enablement, ownership, and business scenario classification manageable through the application database while preserving the existing filesystem-based runtime contract.

**Architecture:** Use a hybrid design. The database is the catalog and management source of truth; the filesystem remains the runtime materialization layer for `SKILL.md` and support files under `/mnt/skills`. During the first implementation phase, write DB changes back to the existing filesystem and `extensions_config.json` compatibility paths so prompt injection, sandbox reads, and subagents keep working.

**Tech Stack:** Python 3.12, SQLAlchemy async ORM, FastAPI, Pydantic, existing DeerFlow `SkillStorage`, SQLite/Postgres initially, MySQL-compatible schema design where practical.

---

## Architecture Decisions

### Decision 1: Keep Filesystem Runtime Materialization

The current runtime depends on skill file paths:

- `SkillStorage.load_skills()` scans `SKILL.md` files.
- The lead agent prompt exposes `/mnt/skills/.../SKILL.md`.
- Subagents read `skill.skill_file.read_text()`.
- Sandbox tools resolve `/mnt/skills` back to a host directory.

Pure DB-backed skill loading would require a virtual file layer or broad runtime changes. For the first version, DB manages the catalog and state, then materializes files for runtime.

### Decision 2: Treat Business Classification As First-Class Metadata

The old 1.0 `category` field is useful, but in DeerFlow 2.0 it must not be confused with `SkillCategory.PUBLIC/CUSTOM`. Use separate fields:

- `runtime_category`: `public` or `custom`
- `business_category`: one primary business scenario, such as `image`, `slides`, `data`, `ops`, `learning`, `other`
- `business_tags_json`: flexible list of scenario labels, such as `["restaurant", "photo-audit", "kfc"]`

This supports both simple filtering and future multi-label recommendation/search.

### Decision 3: DB Source of Truth, Compatibility Mirrors

In phase 1, enabled state is written to DB first, then mirrored to `extensions_config.json`. Skill content is written to DB first for custom skills, then materialized to `skills/custom/<name>/...`.

This keeps existing code working while allowing API/UI to manage skills from DB.

---

## Proposed Schema

### `skills`

Primary skill catalog table.

```text
id                         string primary key, uuid
name                       string(64), unique, required
runtime_category           string(16), required: public/custom
source                     string(32), required: bundled/installed/generated/imported
owner_id                   string(64), nullable
visibility                 string(16), required: public/private
enabled                    bool, required
editable                   bool, required

display_name               string(128), nullable
description                text, required
description_zh             text, nullable
license                    text, nullable
allowed_tools_json         json, nullable
frontmatter_metadata_json  json, nullable

business_category          string(64), nullable
business_tags_json         json, required default []

relative_skill_path        string(512), required
materialized_path          string(512), nullable
package_hash               string(128), nullable
content_hash               string(128), nullable
sync_status                string(32), required: synced/dirty/missing/failed
sync_error                 text, nullable
materialized_at            datetime, nullable
deleted_at                 datetime, nullable
created_at                 datetime, required
updated_at                 datetime, required
```

Indexes:

```text
unique(name)
index(runtime_category, enabled)
index(owner_id, enabled)
index(business_category, enabled)
index(source)
```

For MySQL later, keep JSON fields valid JSON and avoid Postgres-only JSONB assumptions.

### `skill_files`

Stores file-level metadata and optionally content for custom skills.

```text
id              integer primary key
skill_id        string, foreign key skills.id
relative_path   string(512), required
content_text    long text, nullable
content_hash    string(128), required
size_bytes      integer, required
mime_type       string(128), nullable
created_at      datetime, required
updated_at      datetime, required
```

Constraints:

```text
unique(skill_id, relative_path)
```

Initial scope may store only `SKILL.md` content in DB and record support file hashes. Full support-file content can come in a later task if needed.

### `skill_revisions`

Audit and rollback history.

```text
id              integer primary key
skill_id        string, foreign key skills.id
action          string(32): sync/install/edit/delete/rollback/enable/disable/classify
actor_user_id   string(64), nullable
snapshot_json   json, required
created_at      datetime, required
```

### Optional Later: `skill_business_tags`

Start with `business_tags_json` for speed. If filtering/search becomes central, add normalized tags later:

```text
skill_business_tags
- skill_id
- tag
- created_at
unique(skill_id, tag)
index(tag)
```

---

## Task 1: Add Skill Persistence Models

**Files:**
- Create: `backend/packages/harness/deerflow/persistence/skill/__init__.py`
- Create: `backend/packages/harness/deerflow/persistence/skill/model.py`
- Modify: `backend/packages/harness/deerflow/persistence/models/__init__.py`
- Test: `backend/tests/test_skill_catalog_models.py`

**Step 1: Write failing tests**

Add tests that import the new ORM rows and assert table registration:

```python
from deerflow.persistence.base import Base
from deerflow.persistence.skill.model import SkillFileRow, SkillRevisionRow, SkillRow


def test_skill_tables_are_registered():
    assert "skills" in Base.metadata.tables
    assert "skill_files" in Base.metadata.tables
    assert "skill_revisions" in Base.metadata.tables
```

**Step 2: Run the test**

Run:

```powershell
cd backend
uv run pytest tests/test_skill_catalog_models.py -q
```

Expected: fail because models do not exist.

**Step 3: Implement models**

Create `SkillRow`, `SkillFileRow`, and `SkillRevisionRow` using SQLAlchemy columns compatible with SQLite/Postgres and reasonably compatible with MySQL:

- Use `String`, `Text`, `JSON`, `DateTime`, `Boolean`, `ForeignKey`, `Index`, `UniqueConstraint`.
- Use string values instead of database enums.
- Use UUID strings for `skills.id`.

**Step 4: Register models**

Import rows in `backend/packages/harness/deerflow/persistence/models/__init__.py`.

**Step 5: Run tests**

Run:

```powershell
cd backend
uv run pytest tests/test_skill_catalog_models.py -q
```

Expected: pass.

---

## Task 2: Add Skill Catalog Repository

**Files:**
- Create: `backend/packages/harness/deerflow/persistence/skill/sql.py`
- Modify: `backend/packages/harness/deerflow/persistence/skill/__init__.py`
- Test: `backend/tests/test_skill_catalog_repository.py`

**Step 1: Write repository tests**

Cover these behaviors:

- `upsert_skill()` creates a row from parsed skill metadata.
- Re-running `upsert_skill()` updates description, hashes, category, and tags.
- `list_skills()` filters by enabled, runtime category, business category, tags, and owner.
- `set_enabled()` updates DB and creates a revision.
- `set_business_classification()` updates `business_category` and `business_tags_json`.
- Soft delete sets `deleted_at` and disables the skill.

**Step 2: Run tests**

Run:

```powershell
cd backend
uv run pytest tests/test_skill_catalog_repository.py -q
```

Expected: fail because repository does not exist.

**Step 3: Implement repository**

Implement `SkillCatalogRepository` with async methods:

```python
class SkillCatalogRepository:
    async def upsert_skill(...)
    async def get_by_name(...)
    async def list_skills(...)
    async def set_enabled(...)
    async def set_business_classification(...)
    async def soft_delete(...)
    async def append_revision(...)
```

Keep JSON filtering simple in phase 1:

- Filter `business_category` in SQL.
- Filter `business_tags_json` in Python after selecting candidates.

This avoids dialect-specific JSON predicates.

**Step 4: Run tests**

Run:

```powershell
cd backend
uv run pytest tests/test_skill_catalog_repository.py -q
```

Expected: pass.

---

## Task 3: Add Skill Catalog Sync Service

**Files:**
- Create: `backend/packages/harness/deerflow/skills/catalog.py`
- Test: `backend/tests/test_skill_catalog_sync.py`

**Step 1: Write tests**

Cover:

- Scan existing `skills/public` and `skills/custom`.
- Parse `SKILL.md` frontmatter.
- Preserve `enabled` from DB if row already exists.
- Import initial enabled value from `extensions_config.json` only for first sync.
- Derive `runtime_category` from directory.
- Derive `business_category` from existing DB value when present, otherwise from old field or default `other`.
- Set `business_tags_json` to `[]` by default.
- Compute `content_hash` from `SKILL.md`.
- Mark rows missing from filesystem as `sync_status=missing` instead of deleting.

**Step 2: Run tests**

Run:

```powershell
cd backend
uv run pytest tests/test_skill_catalog_sync.py -q
```

Expected: fail.

**Step 3: Implement service**

Implement a service that accepts:

```python
SkillCatalogSyncService(
    storage: SkillStorage,
    repository: SkillCatalogRepository,
    extensions_config: ExtensionsConfig | None = None,
)
```

Methods:

```python
async def sync_from_filesystem(self) -> SkillCatalogSyncResult
async def materialize_skill(self, skill_name: str) -> None
async def mirror_enabled_to_extensions_config(self, skill_name: str) -> None
```

**Step 4: Run tests**

Run:

```powershell
cd backend
uv run pytest tests/test_skill_catalog_sync.py -q
```

Expected: pass.

---

## Task 4: Initialize Skill Catalog At Gateway Startup

**Files:**
- Modify: `backend/app/gateway/deps.py`
- Test: `backend/tests/test_gateway_skill_catalog_startup.py`

**Step 1: Write startup tests**

Create a test that starts the runtime lifecycle with `database.backend=sqlite`, points `skills.path` at a temp directory, and verifies:

- DB rows are created from skill files.
- Startup does not fail when no skills directory exists.
- Existing DB classification fields are preserved on resync.

**Step 2: Run tests**

Run:

```powershell
cd backend
uv run pytest tests/test_gateway_skill_catalog_startup.py -q
```

Expected: fail.

**Step 3: Implement startup sync**

In `deps.py`, after `init_engine_from_config(config.database)` and after session factory is available:

- Create `SkillCatalogRepository(sf)`.
- Attach it to `app.state.skill_catalog_repo`.
- Run `SkillCatalogSyncService(...).sync_from_filesystem()`.

If `database.backend=memory`, skip DB catalog sync and keep current local behavior.

**Step 4: Run tests**

Run:

```powershell
cd backend
uv run pytest tests/test_gateway_skill_catalog_startup.py -q
```

Expected: pass.

---

## Task 5: Move Skills API Listing To DB Catalog

**Files:**
- Modify: `backend/app/gateway/deps.py`
- Modify: `backend/app/gateway/routers/skills.py`
- Test: `backend/tests/test_skills_catalog_router.py`

**Step 1: Write router tests**

Cover:

- `GET /api/skills` returns DB catalog rows when DB catalog is available.
- Response includes `business_category` and `business_tags`.
- Existing response fields remain backward compatible.
- When DB catalog is unavailable, fallback to `LocalSkillStorage.load_skills()`.

**Step 2: Update response model**

Extend `SkillResponse`:

```python
business_category: str | None = None
business_tags: list[str] = Field(default_factory=list)
source: str | None = None
owner_id: str | None = None
```

Do not remove current fields.

**Step 3: Implement DB listing path**

In `list_skills()`:

- Prefer `get_skill_catalog_repo(request)` when available.
- Query DB rows.
- Convert rows to `SkillResponse`.
- Fall back to current storage path if no repo.

**Step 4: Run tests**

Run:

```powershell
cd backend
uv run pytest tests/test_skills_catalog_router.py tests/test_skills_custom_router.py -q
```

Expected: pass.

---

## Task 6: Add Business Classification API

**Files:**
- Modify: `backend/app/gateway/routers/skills.py`
- Test: `backend/tests/test_skills_business_classification_router.py`

**Step 1: Add request model tests**

Cover:

- `PATCH /api/skills/{skill_name}/classification`
- Updates `business_category`.
- Updates `business_tags`.
- Validates tag shape and max length.
- Creates a `skill_revisions` row with action `classify`.

**Step 2: Add models**

Add:

```python
class SkillClassificationUpdateRequest(BaseModel):
    business_category: str | None = None
    business_tags: list[str] = Field(default_factory=list)
```

Validation rules:

- Category: nullable, hyphen-case or simple slug, max 64.
- Tags: max 20 tags.
- Each tag: lowercase slug, max 64.

**Step 3: Implement endpoint**

Endpoint:

```text
PATCH /api/skills/{skill_name}/classification
```

The endpoint should update DB only. No filesystem materialization is required because this classification does not affect runtime files.

**Step 4: Run tests**

Run:

```powershell
cd backend
uv run pytest tests/test_skills_business_classification_router.py -q
```

Expected: pass.

---

## Task 7: DB-First Enable And Disable

**Files:**
- Modify: `backend/app/gateway/routers/skills.py`
- Modify: `backend/packages/harness/deerflow/skills/catalog.py`
- Test: `backend/tests/test_skills_catalog_enablement.py`

**Step 1: Write tests**

Cover:

- `PATCH /api/skills/{skill_name}` writes DB `enabled`.
- It mirrors enabled state to `extensions_config.json`.
- It refreshes the skill prompt cache.
- Runtime still sees the same enabled state through current `LocalSkillStorage.load_skills()`.

**Step 2: Implement DB-first update**

Change the existing update skill endpoint:

- If DB repo exists, call `repo.set_enabled()`.
- Then call `SkillCatalogSyncService.mirror_enabled_to_extensions_config()`.
- Then reload extensions config and refresh prompt cache.
- If DB repo does not exist, keep existing behavior.

**Step 3: Run tests**

Run:

```powershell
cd backend
uv run pytest tests/test_skills_catalog_enablement.py tests/test_skills_custom_router.py -q
```

Expected: pass.

---

## Task 8: DB-First Custom Skill Install, Edit, Delete

**Files:**
- Modify: `backend/app/gateway/routers/skills.py`
- Modify: `backend/packages/harness/deerflow/skills/catalog.py`
- Test: `backend/tests/test_skills_catalog_mutations.py`

**Step 1: Write tests**

Cover:

- Installing a `.skill` creates DB `skills` and `skill_files` rows.
- Installing materializes files to `skills/custom/<name>/`.
- Editing `SKILL.md` updates DB, writes file, updates hashes, and creates revision.
- Deleting custom skill soft-deletes DB row, removes files, and creates revision.
- Built-in/public skill remains non-editable.

**Step 2: Implement install flow**

Keep current archive validation and security scan. After archive is accepted:

- Parse `SKILL.md`.
- Create or update DB rows.
- Materialize files.
- Mirror enabled state.
- Refresh prompt cache.

**Step 3: Implement edit flow**

For custom `SKILL.md` edit:

- Validate content.
- Security scan content.
- Store previous snapshot in `skill_revisions`.
- Write DB content and metadata.
- Materialize file.

**Step 4: Implement delete flow**

For delete:

- Soft-delete DB row.
- Remove local skill directory.
- Mirror disabled state.
- Refresh prompt cache.

**Step 5: Run tests**

Run:

```powershell
cd backend
uv run pytest tests/test_skills_catalog_mutations.py tests/test_skills_installer.py tests/test_skills_custom_router.py -q
```

Expected: pass.

---

## Task 9: Add Migration From 1.0 Skill Table Shape

**Files:**
- Create: `backend/scripts/migrate_legacy_skills_catalog.py`
- Test: `backend/tests/test_migrate_legacy_skills_catalog.py`

**Step 1: Write tests**

Use sample rows shaped like the 1.0 table:

```text
id, name, description, license, enabled, file_path, skill_type,
owner_id, is_public, zip_hash, category, created_at, updated_at
```

Assert mapping:

- `skill_type=builtin` -> `runtime_category=public`, `source=bundled`, `editable=false`
- `skill_type=custom` -> `runtime_category=custom`, `source=imported`, `editable=true`
- `category` -> `business_category`
- `zip_hash` -> `package_hash`
- `file_path` -> converted into `relative_skill_path` when possible
- `is_public` -> `visibility`

**Step 2: Implement migration script**

The script should accept:

```powershell
uv run python backend/scripts/migrate_legacy_skills_catalog.py --input legacy_skills.tsv
```

It should:

- Parse TSV/CSV.
- Normalize names to DeerFlow skill naming rules where possible.
- Preserve invalid names but mark `sync_status=failed` with `sync_error`.
- Insert or update DB rows.

**Step 3: Run tests**

Run:

```powershell
cd backend
uv run pytest tests/test_migrate_legacy_skills_catalog.py -q
```

Expected: pass.

---

## Task 10: Add Frontend Support For Business Classification

**Files:**
- Modify: `frontend/src/core/skills/types.ts`
- Modify: `frontend/src/core/skills/api.ts`
- Modify: `frontend/src/core/skills/hooks.ts`
- Modify: `frontend/src/app/workspace/skills/page.tsx`
- Test: `frontend/tests/unit/core/skills/*.test.ts`

**Step 1: Update types**

Add:

```typescript
business_category?: string | null;
business_tags: string[];
source?: string | null;
owner_id?: string | null;
```

**Step 2: Add API helper**

Add a function for:

```text
PATCH /api/skills/{skill_name}/classification
```

**Step 3: Add UI controls**

In the skills page:

- Show primary business category.
- Show tags.
- Add filter by business category.
- Add edit action for category/tags.

Keep the UI compact and operational; this is a management page, not a landing page.

**Step 4: Run frontend tests**

Run:

```powershell
cd frontend
pnpm test
```

Expected: pass.

---

## Task 11: Add Operational Checks And Documentation

**Files:**
- Modify: `backend/docs/CONFIGURATION.md`
- Modify: `frontend/src/content/en/application/configuration.mdx`
- Modify: `frontend/src/content/zh/application/configuration.mdx`
- Create: `backend/docs/SKILL_CATALOG_DB.md`
- Test: documentation review

**Step 1: Document configuration**

Document:

- DB catalog behavior.
- Compatibility mirror to filesystem and `extensions_config.json`.
- Business category and tags.
- How to resync from filesystem.
- How to migrate old 1.0 rows.

**Step 2: Add troubleshooting**

Include:

- Skill appears in DB but not runtime: check materialization and `sync_status`.
- Skill runtime path missing: run resync.
- Enabled mismatch: check DB row and generated extensions config.

**Step 3: Run backend focused tests**

Run:

```powershell
cd backend
uv run pytest tests/test_skill_catalog_models.py tests/test_skill_catalog_repository.py tests/test_skill_catalog_sync.py tests/test_skills_catalog_router.py tests/test_skills_business_classification_router.py tests/test_skills_catalog_enablement.py tests/test_skills_catalog_mutations.py -q
```

Expected: pass.

---

## Suggested Milestones

### Milestone 1: DB Catalog Mirror

Tasks 1 through 5.

Outcome:

- Existing skills are synced to DB.
- `/api/skills` can list from DB.
- Business category/tags fields exist.
- Runtime remains unchanged.

### Milestone 2: DB Managed State And Classification

Tasks 6 and 7.

Outcome:

- Enable/disable is DB-first.
- Business classification can be edited through API.
- Compatibility mirror keeps existing runtime working.

### Milestone 3: DB Managed Custom Skill Mutations

Task 8.

Outcome:

- Install/edit/delete writes DB first and materializes files.
- Revision history is DB backed.

### Milestone 4: Migration And UI

Tasks 9 through 11.

Outcome:

- 1.0 table can be imported.
- Frontend can filter and manage business scenarios.
- Documentation exists.

---

## Acceptance Criteria

- Existing filesystem skill loading still works.
- Existing public/custom skills appear in DB after startup sync.
- Business classification is available as `business_category` plus `business_tags`.
- API can filter skills by business category.
- Enable/disable writes DB first and remains visible to runtime.
- Custom skill install/edit/delete updates DB and filesystem consistently.
- A failed materialization is visible through `sync_status=failed`.
- Tests cover repository, sync, router, and mutation flows.

---

## Follow-Up Decisions

Decide after Milestone 2:

- Whether `business_tags_json` is enough, or whether to add normalized `skill_business_tags`.
- Whether to keep `extensions_config.json` indefinitely as a compatibility mirror.
- Whether to implement a fully `DbManagedSkillStorage` class for runtime reads.
- Whether MySQL support is required before or after skill catalog DB work.
