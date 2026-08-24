import io
import json
import logging
import re
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.gateway.authz import AuthContext, authenticate, require_permission
from app.gateway.deps import get_config, get_skill_share_repo
from app.gateway.path_utils import resolve_thread_virtual_path
from deerflow.agents.lead_agent.prompt import refresh_skills_system_prompt_cache_async
from deerflow.config.app_config import AppConfig
from deerflow.config.extensions_config import ExtensionsConfig, SkillStateConfig, get_extensions_config, reload_extensions_config
from deerflow.models import create_chat_model
from deerflow.persistence.skill_share.store import SkillShareRepository
from deerflow.skills import Skill
from deerflow.skills.installer import SkillAlreadyExistsError, SkillSecurityScanError
from deerflow.skills.parser import parse_skill_file
from deerflow.skills.security_scanner import scan_skill_content
from deerflow.skills.storage import get_or_new_skill_storage
from deerflow.skills.types import SKILL_MD_FILE, SkillCategory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["skills"])

SKILL_ARCHIVE_UPLOAD_CHUNK_SIZE = 1024 * 1024
MAX_SKILL_ARCHIVE_UPLOAD_BYTES = 100 * 1024 * 1024


def _format_upload_limit(size: int) -> str:
    if size % (1024 * 1024) == 0:
        return f"{size // (1024 * 1024)} MiB"
    return f"{size} bytes"


async def _write_skill_archive_upload_to_temp_file(file: UploadFile, suffix: str) -> Path:
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            tmp_path = Path(tmp_file.name)
            total_size = 0
            while chunk := await file.read(SKILL_ARCHIVE_UPLOAD_CHUNK_SIZE):
                total_size += len(chunk)
                if total_size > MAX_SKILL_ARCHIVE_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Skill archive too large: maximum is {_format_upload_limit(MAX_SKILL_ARCHIVE_UPLOAD_BYTES)}",
                    )
                tmp_file.write(chunk)
        return tmp_path
    except Exception:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


class SkillResponse(BaseModel):
    """Response model for skill information."""

    name: str = Field(..., description="Name of the skill")
    description: str = Field(..., description="Description of what the skill does")
    display_name: str | None = Field(None, description="Display name of the skill (e.g. Chinese name)")
    description_zh: str | None = Field(None, description="Chinese description of the skill")
    license: str | None = Field(None, description="License information")
    category: SkillCategory = Field(..., description="Category of the skill (public or custom)")
    enabled: bool = Field(default=True, description="Whether this skill is enabled")
    download_url: str | None = Field(None, description="Download URL for custom skills; public skills do not support download")
    # Ownership and share metadata.  Only populated for custom skills.
    owner_user_id: str | None = Field(None, description="Owner user id of a custom skill; null for public skills")
    owner_email: str | None = Field(None, description="Owner email of a custom skill; null for public skills")
    shared_with: list[dict] = Field(
        default_factory=list,
        description="Custom-skill share recipients (sharees).  Each entry has keys 'id', 'email', 'system_role'.  Empty for public skills.",
    )
    can_edit: bool = Field(True, description="Whether the caller may mutate this skill.  Shared custom skills are read-only for sharees.")


class SkillShareUserInfo(BaseModel):
    """Lightweight user view for share lists."""

    id: str = Field(..., description="User id.  Matches auth.users.id.")
    email: str = Field(..., description="Unique user email.")
    system_role: str = Field("user", description="Either 'admin' or 'user'.")


class SkillShareListResponse(BaseModel):
    """Full share state of a single custom skill."""

    skill_name: str = Field(..., description="Custom skill name.")
    owner_user_id: str = Field(..., description="Owner of the custom skill.  Shares may only be edited by this user (or admins).")
    owner_email: str = Field(..., description="Owner email for convenience display.")
    sharees: list[SkillShareUserInfo] = Field(default_factory=list, description="Users who currently receive read-only access to the custom skill.")


class SkillShareUpdateRequest(BaseModel):
    """Payload for replacing the entire share list of a custom skill.

    This is a replace, not an append — the server atomically removes any
    existing share rows not present in ``shared_with_user_ids`` and inserts
    rows for any newly listed ids.
    """

    shared_with_user_ids: list[str] = Field(
        default_factory=list,
        description="Full replacement list of user ids who receive read-only access.  Pass an empty list to revoke all shares.",
    )


class SkillShareUpdateResponse(BaseModel):
    skill_name: str
    owner_user_id: str
    owner_email: str
    sharees: list[SkillShareUserInfo]


class SkillsListResponse(BaseModel):
    """Response model for listing all skills."""

    skills: list[SkillResponse]


class SkillUpdateRequest(BaseModel):
    """Request model for updating a skill."""

    enabled: bool = Field(..., description="Whether to enable or disable the skill")


class SkillInstallRequest(BaseModel):
    """Request model for installing a skill from a .skill file."""

    thread_id: str = Field(..., description="The thread ID where the .skill file is located")
    path: str = Field(..., description="Virtual path to the .skill file (e.g., mnt/user-data/outputs/my-skill.skill)")


class SkillInstallResponse(BaseModel):
    """Response model for skill installation."""

    success: bool = Field(..., description="Whether the installation was successful")
    skill_name: str = Field(..., description="Name of the installed skill")
    message: str = Field(..., description="Installation result message")


class SkillUploadErrorDetail(BaseModel):
    code: str = Field(..., description="Machine-readable upload error code")
    message: str = Field(..., description="Human-readable upload error message")
    reason: str = Field(..., description="Specific failure reason")
    can_force: bool = Field(False, description="Whether the user may retry with force=true")


class CustomSkillContentResponse(SkillResponse):
    content: str = Field(..., description="Raw SKILL.md content")


class CustomSkillCreateRequest(BaseModel):
    name: str = Field(..., description="Hyphen-case custom skill name")
    description: str = Field(..., min_length=1, description="Short skill description")
    content: str | None = Field(None, description="Optional SKILL.md content. If omitted, a starter document is generated.")
    allowed_tools: list[str] = Field(default_factory=list, description="Optional tool names to mention in the starter SKILL.md")


class CustomSkillUpdateRequest(BaseModel):
    content: str = Field(..., description="Replacement SKILL.md content")


class SkillAIDraftRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="User brief for the skill to draft")
    name_hint: str | None = Field(None, description="Optional hyphen-case skill name hint")
    description_hint: str | None = Field(None, description="Optional short description hint")
    deep_thinking: bool = Field(False, description="Whether to request a more deliberate draft")
    skill_creator_name: str | None = Field(None, description="Optional creator profile name")


class SkillAIDraftResponse(BaseModel):
    name: str = Field(..., description="Suggested hyphen-case skill name")
    description: str = Field(..., description="Suggested skill description")
    content: str = Field(..., description="Generated SKILL.md draft")


class CustomSkillHistoryResponse(BaseModel):
    history: list[dict]


class CustomSkillVersionsResponse(BaseModel):
    versions: list[dict]


class CustomSkillVersionCreateRequest(BaseModel):
    action: str = Field(default="edit", description="Version action label, e.g. edit/publish/install/create/restore.")
    message: str | None = Field(None, description="Optional human note for this snapshot.")
    thread_id: str | None = Field(None, description="Optional thread id that produced this snapshot.")


class CustomSkillVersionRestoreResponse(BaseModel):
    version: dict = Field(..., description="Created version record representing the restored state.")


class CustomSkillFileEntry(BaseModel):
    path: str = Field(..., description="Relative path from the skill root")
    type: str = Field(..., description="Either file or directory")
    size: int | None = Field(None, description="File size in bytes")


class CustomSkillFilesResponse(BaseModel):
    files: list[CustomSkillFileEntry]


class CustomSkillFileContentResponse(BaseModel):
    path: str = Field(..., description="Relative path from the skill root")
    content: str = Field(..., description="File text content")


class CustomSkillFileWriteRequest(BaseModel):
    path: str = Field(..., description="Relative path from the skill root")
    content: str = Field(default="", description="File text content")


class CustomSkillDirectoryCreateRequest(BaseModel):
    path: str = Field(..., description="Relative directory path from the skill root")


class CustomSkillUploadResponse(BaseModel):
    paths: list[str] = Field(default_factory=list, description="Uploaded relative paths")


class SkillRollbackRequest(BaseModel):
    history_index: int = Field(default=-1, description="History entry index to restore from, defaulting to the latest change.")


def _slugify_skill_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    return normalized[:64].strip("-") or "custom-skill"


def _extract_skill_markdown(raw: str) -> str:
    raw = raw.strip()
    fence_match = re.match(r"^```(?:markdown|md)?\s*\n?(.*?)\n?\s*```$", raw, re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()
    start = raw.find("---")
    if start > 0:
        raw = raw[start:]
    return raw.strip()


def _build_default_skill_content(name: str, description: str, allowed_tools: list[str] | None = None) -> str:
    tool_section = ""
    tools = [tool.strip() for tool in allowed_tools or [] if tool.strip()]
    if tools:
        tool_lines = "\n".join(f"- `{tool}`" for tool in tools)
        tool_section = f"\n## Tool Guidance\n{tool_lines}\n"
    return f"""---
name: {name}
description: {description}
---

Use this skill when the user asks for {description}.

## Workflow
1. Clarify the user's target outcome when the request is ambiguous.
2. Gather only the context needed for the task.
3. Execute the task using the repository's existing conventions.
4. Verify the result before reporting completion.
{tool_section}
## Output
- Keep the final answer concise.
- Include file paths, commands, or artifacts that are useful for review.
"""


async def _generate_ai_skill_draft(request: SkillAIDraftRequest, config: AppConfig) -> SkillAIDraftResponse:
    name_hint = _slugify_skill_name(request.name_hint or request.prompt[:48])
    description_hint = (request.description_hint or request.prompt).strip()[:160]
    system_prompt = (
        "You create DeerFlow SKILL.md files. Return only one markdown document. "
        "The document must start with YAML frontmatter containing exactly a hyphen-case name and a short description. "
        "Then write concise instructions with sections that explain when to use the skill, workflow, and output expectations. "
        "Do not include code fences around the document."
    )
    user_prompt = (
        f"Draft a SKILL.md.\nName hint: {name_hint}\nDescription hint: {description_hint}\nCreator profile: {request.skill_creator_name or 'default'}\nDeep thinking requested: {request.deep_thinking}\n\nUser brief:\n{request.prompt}"
    )
    model = create_chat_model(thinking_enabled=request.deep_thinking, app_config=config, attach_tracing=False)
    response = await model.ainvoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)],
        config={"run_name": "skill_ai_draft"},
    )
    content = _extract_skill_markdown(str(getattr(response, "content", "") or ""))
    if not content:
        content = _build_default_skill_content(name_hint, description_hint)
    parsed_name = name_hint
    description = description_hint
    name_match = re.search(r"^name:\s*([a-z0-9][a-z0-9-]*)\s*$", content, re.MULTILINE)
    description_match = re.search(r"^description:\s*(.+?)\s*$", content, re.MULTILINE)
    if name_match:
        parsed_name = _slugify_skill_name(name_match.group(1))
    if description_match:
        description = description_match.group(1).strip().strip("\"'")
    return SkillAIDraftResponse(name=parsed_name, description=description, content=content)


async def _require_system_admin(http_request: Request) -> None:
    auth: AuthContext | None = getattr(http_request.state, "auth", None)
    if auth is None:
        auth = await authenticate(http_request)
        http_request.state.auth = auth
    if not auth.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not auth.has_permission("system", "admin"):
        raise HTTPException(status_code=403, detail="Permission denied: system:admin")


def _skill_to_response(
    skill: Skill,
    *,
    owner_email: str | None = None,
    shared_with: list[SkillShareUserInfo] | None = None,
    can_edit: bool = True,
) -> SkillResponse:
    """Convert a Skill object to a SkillResponse, filling in optional share metadata.

    ``owner_email`` / ``shared_with`` / ``can_edit`` are honoured only for
    ``SkillCategory.CUSTOM`` skills; for public skills those fields are
    coerced to ``None`` / ``[]`` / ``True`` (admins always edit public).
    """
    download_url: str | None = None
    effective_owner_id: str | None = None
    effective_owner_email: str | None = None
    effective_shared_with: list[dict] = []
    effective_can_edit: bool = True
    if skill.category == SkillCategory.CUSTOM:
        download_url = f"/api/skills/custom/{skill.name}/download"
        effective_owner_id = skill.owner_user_id
        effective_owner_email = owner_email
        effective_shared_with = [s.model_dump(mode="json") for s in (shared_with or [])]
        effective_can_edit = can_edit
    return SkillResponse(
        name=skill.name,
        description=skill.description,
        display_name=skill.display_name,
        description_zh=skill.description_zh,
        license=skill.license,
        category=skill.category,
        enabled=skill.enabled,
        download_url=download_url,
        owner_user_id=effective_owner_id,
        owner_email=effective_owner_email,
        shared_with=effective_shared_with,
        can_edit=effective_can_edit,
    )


async def _user_provider():
    """Access the auth local provider so routes can resolve id→email lookups.

    Lazily imported because user-listing code only runs in the Gateway
    process, while the same module is also imported from sandbox workers
    that do not initialise SQLAlchemy.
    """
    from app.gateway.deps import get_local_provider

    return get_local_provider()


async def _build_user_email_index(user_ids: set[str]) -> dict[str, tuple[str, str]]:
    """Return mapping user_id → (email, system_role) for the given ids."""
    if not user_ids:
        return {}
    provider = await _user_provider()
    users = await provider.repository.list_users()
    result: dict[str, tuple[str, str]] = {}
    # Normalise incoming ids to strings in case callers pass UUID objects,
    # then lower-case both sides so that "ABC..." matches "abc...".
    normalised_ids = {str(uid).lower() for uid in user_ids}
    logger.info("_build_user_email_index: looking up %d ids, %d users available", len(normalised_ids), len(users))
    for u in users:
        uid_str = str(u.id).lower()
        if uid_str in normalised_ids:
            result[uid_str] = (str(u.email), str(getattr(u, "system_role", "user")))
    matched = len(result)
    logger.info("_build_user_email_index: matched %d/%d ids", matched, len(normalised_ids))
    if matched < len(normalised_ids):
        missing = normalised_ids - set(result.keys())
        logger.warning("_build_user_email_index: unresolved ids: %s", missing)
    return result


async def _fetch_custom_skill_sharees_and_owner(
    share_repo: SkillShareRepository,
    *,
    skill_names: list[str] | None = None,
    shared_with_user_id: str | None = None,
) -> tuple[dict[str, list[SkillShareUserInfo]], dict[str, str]]:
    """Return (sharees_by_skill_name, owner_email_by_owner_id).

    The share repo uses raw user ids; this helper hydrates those ids to
    email + system_role using the auth user repository so response payloads
    are directly renderable by the share dialog UI.
    """
    # 1) load relevant share rows from the DB
    if skill_names is not None:
        grants = []
        for name in skill_names:
            grants.extend(await share_repo.list_sharees_for_skill(name))
    else:
        grants = await share_repo.list_shares_for_shared_user(shared_with_user_id or "")

    # 2) collect all distinct user ids we need to resolve
    sharee_ids: set[str] = set()
    owner_ids: set[str] = set()
    for g in grants:
        if g.shared_with_user_id:
            sharee_ids.add(g.shared_with_user_id)
        if g.owner_user_id:
            owner_ids.add(g.owner_user_id)

    user_email_index = await _build_user_email_index(sharee_ids | owner_ids)

    # 3) group sharees by skill name
    sharees_by_skill: dict[str, list[SkillShareUserInfo]] = {}
    owner_email_by_owner_id: dict[str, str] = {}
    for oid in owner_ids:
        if oid.lower() in user_email_index:
            owner_email_by_owner_id[oid] = user_email_index[oid.lower()][0]
    for g in grants:
        if not g.shared_with_user_id:
            continue
        lookup_key = g.shared_with_user_id.lower()
        email, role = user_email_index.get(lookup_key, (g.shared_with_user_id, "user"))
        info = SkillShareUserInfo(id=g.shared_with_user_id, email=email, system_role=role)
        sharees_by_skill.setdefault(g.skill_name, []).append(info)
    return sharees_by_skill, owner_email_by_owner_id


async def _load_skills_share_aware(
    *,
    config: AppConfig,
    share_repo: SkillShareRepository,
    user_id: str | None,
    is_admin: bool,
) -> list[tuple[Skill, list[SkillShareUserInfo], str | None, bool]]:
    """Load skills with owner/share-aware visibility.

    Returns list of tuples:
      ``(skill, sharees_for_this_skill, owner_email, can_edit_for_caller)``.

    The rules match the design v2 permissions matrix:

      * Public skills: visible if enabled (or admin sees disabled too)
      * Own custom skills: always visible, can_edit=True
      * Shared-with-me custom skills: visible, can_edit=False
      * Other users' unshared custom skills: hidden
    """
    storage = get_or_new_skill_storage(app_config=config)
    # Storage-level owner isolation already filters "own custom skills" via
    # _can_access_custom_skill_dir.  Public skills are all enumerated too.
    base_skills = storage.load_skills(enabled_only=False)

    # Next, augment base set with "shared with me" custom skills I don't own.
    if user_id:
        shared_grants = await share_repo.list_shares_for_shared_user(user_id)
        owned_names = {s.name for s in base_skills if s.category == SkillCategory.CUSTOM}
        shared_to_add: list[Skill] = []
        public_name_set = {s.name for s in base_skills if s.category == SkillCategory.PUBLIC}
        for grant in shared_grants:
            if grant.skill_name in owned_names or grant.skill_name in public_name_set:
                continue
            skill_dir = storage.get_custom_skill_dir(grant.skill_name)
            skill_md = skill_dir / SKILL_MD_FILE
            if not skill_md.exists():
                # DB state drifted — grant references a skill that no longer
                # exists on disk.  Skip; cleanup is a separate admin concern.
                continue
            parsed = parse_skill_file(
                skill_md,
                SkillCategory.CUSTOM,
                owner_user_id=grant.owner_user_id,
            )
            if parsed is None:
                continue
            shared_to_add.append(parsed)
        base_skills.extend(shared_to_add)

    # Merge enabled state the same way storage's native load_skills does.
    try:
        extensions_config = ExtensionsConfig.from_file()
        for skill in base_skills:
            skill.enabled = extensions_config.is_skill_enabled(skill.name, skill.category)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to merge enabled extensions config in share-aware loader: %s", e)

    # Hydrate share+owner metadata for all custom skills in result set.
    custom_names = [s.name for s in base_skills if s.category == SkillCategory.CUSTOM]
    sharees_by_skill, owner_email_by_owner_id = await _fetch_custom_skill_sharees_and_owner(
        share_repo,
        skill_names=custom_names,
    )
    # Also resolve owners of my custom skills (they may have no share rows)
    unresolved_owner_ids = {
        s.owner_user_id for s in base_skills if s.category == SkillCategory.CUSTOM and s.owner_user_id
    }
    if unresolved_owner_ids - set(owner_email_by_owner_id.keys()):
        email_index = await _build_user_email_index(unresolved_owner_ids)
        owner_email_by_owner_id.update({oid: info[0] for oid, info in email_index.items()})

    # Apply final visibility filter and assemble tuples
    result: list[tuple[Skill, list[SkillShareUserInfo], str | None, bool]] = []
    for skill in base_skills:
        # Visibility rule 1: public skills are filtered by enabled unless admin
        if skill.category == SkillCategory.PUBLIC:
            if not is_admin and not skill.enabled:
                continue
            result.append((skill, [], None, True))
            continue
        # Custom skill path
        owner_id = skill.owner_user_id
        owner_email = owner_email_by_owner_id.get(owner_id.lower() if owner_id else "") if owner_id else None
        sharees = sharees_by_skill.get(skill.name, [])
        if is_admin:
            can_edit = True
            visible = True
        elif owner_id and user_id and owner_id.lower() == user_id.lower():
            can_edit = True
            visible = True
        else:
            # Shared-with-me (read-only) or invisible
            is_shared_to_me = any(s.id.lower() == (user_id or "").lower() for s in sharees)
            can_edit = False
            visible = is_shared_to_me
        if not visible:
            continue
        result.append((skill, sharees, owner_email, can_edit))

    result.sort(key=lambda r: r[0].name)
    return result


def _is_admin_auth(auth: AuthContext | None) -> bool:
    user = None if auth is None else auth.user
    return bool(user is not None and getattr(user, "system_role", None) == "admin")


def _skills_visible_to_caller(skills: list[Skill], *, is_admin: bool) -> list[Skill]:
    """Admins see every skill; others only see enabled skills and all custom skills.

    Disabled public skills are platform-gated and hidden from non-admins so they
    do not appear in the catalog. Custom skills stay visible even when disabled
    so owners can re-enable them in the UI.
    """
    if is_admin:
        return skills
    return [skill for skill in skills if skill.enabled or skill.category == SkillCategory.CUSTOM]


async def _resolve_auth(request: Request) -> AuthContext:
    auth: AuthContext | None = getattr(request.state, "auth", None)
    if auth is None:
        auth = await authenticate(request)
        request.state.auth = auth
    return auth


@router.get(
    "/skills",
    response_model=SkillsListResponse,
    summary="List All Skills",
    description=(
        "Retrieve the caller's visible skills.  Public skills appear when "
        "enabled (admins see disabled public skills too).  Custom skills "
        "include both the caller's own skills and custom skills shared with "
        "the caller by another user.  Custom skills that were shared are "
        "marked ``can_edit=False`` so the UI presents them read-only."
    ),
)
async def list_skills(
    request: Request,
    config: AppConfig = Depends(get_config),
    share_repo: SkillShareRepository = Depends(get_skill_share_repo),
) -> SkillsListResponse:
    try:
        auth = await _resolve_auth(request)
        user_id = None if auth.user is None else str(getattr(auth.user, "id", None))
        is_admin = _is_admin_auth(auth)
        rows = await _load_skills_share_aware(
            config=config,
            share_repo=share_repo,
            user_id=user_id,
            is_admin=is_admin,
        )
        return SkillsListResponse(
            skills=[
                _skill_to_response(skill, owner_email=owner_email, shared_with=sharees, can_edit=can_edit)
                for skill, sharees, owner_email, can_edit in rows
            ]
        )
    except Exception as e:
        logger.error(f"Failed to load skills: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to load skills: {str(e)}")


@router.post(
    "/skills/install",
    response_model=SkillInstallResponse,
    summary="Install Skill",
    description="Install a skill from a .skill file (ZIP archive) located in the thread's user-data directory.",
)
async def install_skill(request: SkillInstallRequest, config: AppConfig = Depends(get_config)) -> SkillInstallResponse:
    try:
        skill_file_path = resolve_thread_virtual_path(request.thread_id, request.path)
        storage = get_or_new_skill_storage(app_config=config)
        result = await storage.ainstall_skill_from_archive(skill_file_path)
        skill_name = result.get("skill_name")
        if skill_name:
            try:
                storage.create_skill_version(
                    skill_name,
                    action="install",
                    author="human",
                    message=f"installed from thread artifact: {request.path}",
                    thread_id=request.thread_id,
                )
            except Exception as e:
                logger.warning("Failed to create install version snapshot for %s: %s", skill_name, e, exc_info=True)
        await refresh_skills_system_prompt_cache_async()
        return SkillInstallResponse(**result)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except SkillAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to install skill: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to install skill: {str(e)}")


@router.get("/skills/custom", response_model=SkillsListResponse, summary="List Custom Skills")
async def list_custom_skills(
    request: Request,
    config: AppConfig = Depends(get_config),
    share_repo: SkillShareRepository = Depends(get_skill_share_repo),
) -> SkillsListResponse:
    try:
        auth = await _resolve_auth(request)
        user_id = None if auth.user is None else str(getattr(auth.user, "id", None))
        is_admin = _is_admin_auth(auth)
        rows = await _load_skills_share_aware(
            config=config,
            share_repo=share_repo,
            user_id=user_id,
            is_admin=is_admin,
        )
        return SkillsListResponse(
            skills=[
                _skill_to_response(skill, owner_email=owner_email, shared_with=sharees, can_edit=can_edit)
                for skill, sharees, owner_email, can_edit in rows
                if skill.category == SkillCategory.CUSTOM
            ]
        )
    except Exception as e:
        logger.error("Failed to list custom skills: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list custom skills: {str(e)}")


@router.post("/skills/custom", response_model=CustomSkillContentResponse, summary="Create Custom Skill")
async def create_custom_skill(request: CustomSkillCreateRequest, config: AppConfig = Depends(get_config)) -> CustomSkillContentResponse:
    try:
        storage = get_or_new_skill_storage(app_config=config)
        skill_name = storage.validate_skill_name(request.name)
        if storage.custom_skill_exists(skill_name) or storage.public_skill_exists(skill_name):
            raise SkillAlreadyExistsError(f"Skill '{skill_name}' already exists")

        content = request.content or _build_default_skill_content(skill_name, request.description.strip(), request.allowed_tools)
        storage.validate_skill_markdown_content(skill_name, content)
        scan = await scan_skill_content(content, executable=False, location=f"{skill_name}/{SKILL_MD_FILE}", app_config=config)
        if scan.decision == "block":
            raise HTTPException(status_code=400, detail=f"Security scan blocked the create: {scan.reason}")

        storage.write_custom_skill(skill_name, SKILL_MD_FILE, content)
        storage.append_history(
            skill_name,
            {
                "action": "human_create",
                "author": "human",
                "thread_id": None,
                "file_path": SKILL_MD_FILE,
                "prev_content": None,
                "new_content": content,
                "scanner": {"decision": scan.decision, "reason": scan.reason},
            },
        )
        try:
            storage.create_skill_version(
                skill_name,
                action="create",
                author="human",
                message="created via API",
                thread_id=None,
            )
        except Exception as e:
            logger.warning("Failed to create create-version snapshot for %s: %s", skill_name, e, exc_info=True)
        await refresh_skills_system_prompt_cache_async()
        return await get_custom_skill(skill_name, config)
    except SkillAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to create custom skill %s: %s", request.name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create custom skill: {str(e)}")


@router.post("/skills/custom/ai-draft", response_model=SkillAIDraftResponse, summary="Draft Custom Skill With AI")
async def draft_custom_skill_with_ai(request: SkillAIDraftRequest, config: AppConfig = Depends(get_config)) -> SkillAIDraftResponse:
    try:
        return await _generate_ai_skill_draft(request, config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to draft custom skill with AI: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to draft custom skill: {str(e)}")


@router.post("/skills/upload", response_model=SkillInstallResponse, summary="Upload Skill Archive")
async def upload_skill_archive(
    file: UploadFile = File(...),
    force: bool = Query(False, description="Skip user-overridable skill security scan checks after explicit user confirmation."),
    config: AppConfig = Depends(get_config),
) -> SkillInstallResponse:
    filename = file.filename or "skill.skill"
    suffix = Path(filename).suffix.lower()
    if suffix not in {".skill", ".zip"}:
        raise HTTPException(status_code=400, detail="File must have .skill or .zip extension")
    try:
        tmp_path = await _write_skill_archive_upload_to_temp_file(file, suffix)
        try:
            storage = get_or_new_skill_storage(app_config=config)
            result = await storage.ainstall_skill_from_archive(tmp_path, skip_security_scan=force)
            skill_name = result.get("skill_name")
            if skill_name:
                try:
                    storage.create_skill_version(
                        skill_name,
                        action="install",
                        author="human",
                        message=f"uploaded archive: {filename}",
                        thread_id=None,
                    )
                except Exception as e:
                    logger.warning("Failed to create upload-install version snapshot for %s: %s", skill_name, e, exc_info=True)
        finally:
            tmp_path.unlink(missing_ok=True)
        await refresh_skills_system_prompt_cache_async()
        return SkillInstallResponse(**result)
    except SkillAlreadyExistsError as e:
        detail = SkillUploadErrorDetail(
            code="skill_already_exists",
            message=str(e),
            reason="A skill with this name already exists. Please choose a different name or delete the existing skill first.",
            can_force=False,
        )
        raise HTTPException(status_code=409, detail=detail.model_dump())
    except SkillSecurityScanError as e:
        detail = SkillUploadErrorDetail(
            code="security_scan_failed",
            message=str(e),
            reason=e.reason,
            can_force=e.can_force,
        )
        raise HTTPException(status_code=400, detail=detail.model_dump())
    except HTTPException:
        raise
    except ValueError as e:
        detail = SkillUploadErrorDetail(
            code="invalid_archive",
            message=str(e),
            reason="The uploaded file is not a valid skill archive.",
            can_force=False,
        )
        raise HTTPException(status_code=400, detail=detail.model_dump())
    except Exception as e:
        logger.error("Failed to upload skill archive: %s", e, exc_info=True)
        detail = SkillUploadErrorDetail(
            code="upload_failed",
            message=str(e),
            reason="An unexpected error occurred while processing the uploaded archive.",
            can_force=False,
        )
        raise HTTPException(status_code=500, detail=detail.model_dump())
    finally:
        await file.close()


@router.get(
    "/skills/public/{skill_name}",
    response_model=CustomSkillContentResponse,
    summary="Get Public Skill Content (Admin)",
    description="Read SKILL.md for a public skill. Restricted to admin users.",
)
@require_permission("system", "admin")
async def get_public_skill(skill_name: str, request: Request, config: AppConfig = Depends(get_config)) -> CustomSkillContentResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        skills = get_or_new_skill_storage(app_config=config).load_skills(enabled_only=False)
        skill = next((s for s in skills if s.name == skill_name and s.category == SkillCategory.PUBLIC), None)
        if skill is None:
            raise HTTPException(status_code=404, detail=f"Public skill '{skill_name}' not found")
        return CustomSkillContentResponse(**_skill_to_response(skill).model_dump(), content=get_or_new_skill_storage(app_config=config).read_public_skill(skill_name))
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Failed to get public skill %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get public skill: {str(e)}")


@router.get("/skills/custom/{skill_name}", response_model=CustomSkillContentResponse, summary="Get Custom Skill Content")
async def get_custom_skill(
    skill_name: str,
    request: Request,
    config: AppConfig = Depends(get_config),
    share_repo: SkillShareRepository = Depends(get_skill_share_repo),
) -> CustomSkillContentResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        auth = await _resolve_auth(request)
        user_id = None if auth.user is None else str(getattr(auth.user, "id", None))
        is_admin = _is_admin_auth(auth)
        rows = await _load_skills_share_aware(
            config=config,
            share_repo=share_repo,
            user_id=user_id,
            is_admin=is_admin,
        )
        match = next(
            (row for row in rows if row[0].name == skill_name and row[0].category == SkillCategory.CUSTOM),
            None,
        )
        if match is None:
            raise HTTPException(status_code=404, detail=f"Custom skill '{skill_name}' not found")
        skill, sharees, owner_email, can_edit = match

        # Read SKILL.md content.  Owners go through the validated
        # storage.read_custom_skill path; sharees / admins bypass the
        # owner-isolation check and read straight off disk since we have
        # already confirmed visibility via the share-aware loader.
        storage = get_or_new_skill_storage(app_config=config)
        try:
            content = storage.read_custom_skill(skill_name)
        except FileNotFoundError:
            # storage enforces owner isolation → fall back to direct read.
            skill_dir = storage.get_custom_skill_dir(skill_name)
            skill_md = skill_dir / SKILL_MD_FILE
            if not skill_md.exists():
                raise HTTPException(status_code=404, detail=f"Custom skill '{skill_name}' not found")
            content = skill_md.read_text(encoding="utf-8")
        return CustomSkillContentResponse(
            **_skill_to_response(
                skill,
                owner_email=owner_email,
                shared_with=sharees,
                can_edit=can_edit,
            ).model_dump(),
            content=content,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get custom skill %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get custom skill: {str(e)}")


@router.get("/skills/custom/{skill_name}/files", response_model=CustomSkillFilesResponse, summary="List Custom Skill Files")
async def list_custom_skill_files(skill_name: str, config: AppConfig = Depends(get_config)) -> CustomSkillFilesResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        storage = get_or_new_skill_storage(app_config=config)
        files = storage.list_custom_skill_files(skill_name)
        return CustomSkillFilesResponse(files=[CustomSkillFileEntry(**entry) for entry in files])
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to list files for custom skill %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list custom skill files: {str(e)}")


def _collect_skill_files_internal(skill_dir: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for path in sorted(skill_dir.rglob("*")):
        if path.is_file() and not any(part.startswith(".") for part in path.relative_to(skill_dir).parts):
            arcname = str(path.relative_to(skill_dir))
            files.append((arcname, path))
    return files


def _build_skill_zip_internal(skill_dir: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, filepath in _collect_skill_files_internal(skill_dir):
            zf.write(filepath, arcname)
    return buf.getvalue()


@router.get("/skills/custom/{skill_name}/download", summary="Download Custom Skill")
@require_permission("skills", "read")
async def download_custom_skill(
    skill_name: str,
    request: Request,
    config: AppConfig = Depends(get_config),
):
    """Download a custom skill as a .skill (ZIP) archive.

    Only the owner of the custom skill (or an admin) can download it.  Public
    skills intentionally do not support download.
    """
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        auth = await _resolve_auth(request)
        storage = get_or_new_skill_storage(app_config=config)
        skills = storage.load_skills(enabled_only=False)
        skill = next((s for s in skills if s.name == skill_name and s.category == SkillCategory.CUSTOM), None)
        if skill is None:
            raise HTTPException(status_code=404, detail=f"Custom skill '{skill_name}' not found")
        # Owner visibility: non-admins only see their own custom skills via the
        # owner-isolated storage, so we just double-check the visibility rule.
        visible = _skills_visible_to_caller([skill], is_admin=_is_admin_auth(auth))
        if not visible:
            raise HTTPException(status_code=404, detail=f"Custom skill '{skill_name}' not found")
        skill_dir = storage.get_custom_skill_dir(skill_name)
        if not skill_dir.exists() or not skill_dir.is_dir():
            raise HTTPException(status_code=404, detail=f"Custom skill '{skill_name}' has no files on disk")
        zip_bytes = _build_skill_zip_internal(skill_dir)
        filename = f"{skill_name}.zip"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return StreamingResponse(io.BytesIO(zip_bytes), media_type="application/zip", headers=headers)
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Failed to download custom skill %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to download custom skill: {str(e)}")


# ---------------------------------------------------------------------------
# Custom-skill share grants
# ---------------------------------------------------------------------------


async def _enforce_share_admin(
    request: Request,
    skill_name: str,
    *,
    config: AppConfig,
) -> tuple[str, AuthContext, str]:
    """Return (skill_name, auth, owner_user_id) for share-admin operations.

    Only the owner of the custom skill or a system admin may mutate shares.
    The function intentionally raises 404 (not 403) for skills the caller
    can't administer so existence of arbitrary custom skills is not leaked.
    """
    auth = await _resolve_auth(request)
    is_admin = _is_admin_auth(auth)
    caller_id = None if auth.user is None else str(getattr(auth.user, "id", None))
    logger.info("_enforce_share_admin: skill_name=%s, caller_id=%s, is_admin=%s",
                skill_name, caller_id, is_admin)
    storage = get_or_new_skill_storage(app_config=config)
    all_skills = storage.load_skills(enabled_only=False)
    logger.info("_enforce_share_admin: loaded %d skills total", len(all_skills))
    # Owner path: storage native access succeeds
    owned_skills = [s for s in all_skills
                    if s.name == skill_name and s.category == SkillCategory.CUSTOM]
    logger.info("_enforce_share_admin: owned_skills count=%d, names=%s",
                len(owned_skills), [s.name for s in owned_skills])
    if owned_skills:
        owner_id = owned_skills[0].owner_user_id
        logger.info("_enforce_share_admin: owner_id=%s, owner_id_type=%s",
                    owner_id, type(owner_id).__name__)
        if owner_id is None:
            logger.info("_enforce_share_admin: owner_id is None, is_admin=%s", is_admin)
            if is_admin:
                return skill_name, auth, ""
            raise HTTPException(status_code=404, detail=f"Custom skill '{skill_name}' not found")
        logger.info("_enforce_share_admin: checking ownership: caller_id=%s vs owner_id=%s (matched=%s)",
                    caller_id, owner_id, caller_id and caller_id.lower() == owner_id.lower())
        if is_admin or (caller_id and caller_id.lower() == owner_id.lower()):
            return skill_name, auth, owner_id
        raise HTTPException(status_code=404, detail=f"Custom skill '{skill_name}' not found")
    logger.info("_enforce_share_admin: owned_skills empty, is_admin=%s, caller_id=%s", is_admin, caller_id)
    # Fallback: try direct disk-based ownership check (handles cases where
    # enforce_owner_isolation filtered out the skill due to contextvar issues)
    skill_dir = storage.get_custom_skill_dir(skill_name)
    if not skill_dir.exists() or not (skill_dir / SKILL_MD_FILE).exists():
        if not is_admin:
            raise HTTPException(status_code=404, detail=f"Custom skill '{skill_name}' not found")
        return skill_name, auth, ""
    from deerflow.skills.storage.local_skill_storage import LocalSkillStorage

    owner_id = None
    if isinstance(storage, LocalSkillStorage):
        raw = storage._read_custom_skill_owner(skill_dir)
        owner_id = raw or ""
    logger.info("_enforce_share_admin: fallback disk check: owner_id=%s, caller_id=%s", owner_id, caller_id)
    if is_admin:
        return skill_name, auth, owner_id or ""
    # Non-admin: verify ownership via disk metadata
    if caller_id and owner_id and caller_id.lower() == owner_id.lower():
        return skill_name, auth, owner_id
    raise HTTPException(status_code=404, detail=f"Custom skill '{skill_name}' not found")


@router.get(
    "/skills/custom/{skill_name}/shares",
    response_model=SkillShareListResponse,
    summary="List Sharees of a Custom Skill",
    description=(
        "Return the custom skill's current sharees.  Requires ownership "
        "(or system:admin).  Used by the share dialog to pre-populate the "
        "right-hand (already shared) column."
    ),
)
async def list_custom_skill_shares(
    skill_name: str,
    request: Request,
    config: AppConfig = Depends(get_config),
    share_repo: SkillShareRepository = Depends(get_skill_share_repo),
) -> SkillShareListResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        logger.info("list_custom_skill_shares: skill_name=%s", skill_name)
        _skill_name, _auth, owner_user_id = await _enforce_share_admin(request, skill_name, config=config)
        logger.info("list_custom_skill_shares: owner_user_id=%s", owner_user_id)
        user_email_index = await _build_user_email_index({owner_user_id} if owner_user_id else set())
        owner_email = user_email_index.get(owner_user_id.lower(), ("", ""))[0] if owner_user_id else ""
        if not owner_email:
            provider = await _user_provider()
            user = await provider.repository.get_user_by_id(owner_user_id) if owner_user_id else None
            if user is not None:
                owner_email = str(user.email)
                logger.info("list_custom_skill_shares: resolved owner_email=%s via get_user_by_id", owner_email)
        sharee_rows = await share_repo.list_sharees_for_skill(skill_name)
        logger.info("list_custom_skill_shares: found %d sharee rows in DB", len(sharee_rows))
        for row in sharee_rows:
            logger.info("list_custom_skill_shares: row skill=%s owner=%s shared_with=%s",
                        row.skill_name, row.owner_user_id, row.shared_with_user_id)
        sharee_ids = {r.shared_with_user_id for r in sharee_rows if r.shared_with_user_id}
        logger.info("Building email index for %d sharee ids: %s", len(sharee_ids), sharee_ids)
        email_index = await _build_user_email_index(sharee_ids)
        logger.info("Email index contains %d entries: %s", len(email_index), list(email_index.keys()))
        sharees = []
        for r in sharee_rows:
            if not r.shared_with_user_id:
                continue
            lookup_key = r.shared_with_user_id.lower()
            info = email_index.get(lookup_key, (r.shared_with_user_id, "user"))
            matched = info[0] != r.shared_with_user_id
            logger.info("list_custom_skill_shares: sharee id=%s resolved email=%s role=%s (matched=%s)",
                        r.shared_with_user_id, info[0], info[1], matched)
            sharees.append(
                SkillShareUserInfo(
                    id=r.shared_with_user_id,
                    email=info[0],
                    system_role=info[1],
                )
            )
        sharees.sort(key=lambda s: s.email)
        logger.info("list_custom_skill_shares: returning %d sharees", len(sharees))
        return SkillShareListResponse(
            skill_name=skill_name,
            owner_user_id=owner_user_id,
            owner_email=owner_email,
            sharees=sharees,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to list sharees for custom skill %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list sharees: {str(e)}")


@router.put(
    "/skills/custom/{skill_name}/shares",
    response_model=SkillShareUpdateResponse,
    summary="Replace Share List of a Custom Skill",
    description=(
        "Atomically replace the share list.  Pass an empty list to revoke "
        "all shares.  The requester must be the owner (or a system admin). "
        "The server rejects any payload that would share the skill with its "
        "own owner because self-sharing is a no-op at the visibility layer "
        "and would produce misleading entries in the dialog UI."
    ),
)
async def replace_custom_skill_shares(
    skill_name: str,
    payload: SkillShareUpdateRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    share_repo: SkillShareRepository = Depends(get_skill_share_repo),
) -> SkillShareUpdateResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        _skill_name, auth, owner_user_id = await _enforce_share_admin(request, skill_name, config=config)

        # Resolve all candidate user ids upfront so we can validate they
        # exist and reject self-sharing with a clear message.
        raw_ids = list({uid for uid in payload.shared_with_user_ids if uid})
        provider = await _user_provider()
        users = await provider.repository.list_users()
        valid_ids = {str(u.id).lower(): (str(u.email), str(getattr(u, "system_role", "user"))) for u in users}
        # Validate all ids exist (case-insensitive)
        missing = [uid for uid in raw_ids if uid.lower() not in valid_ids]
        if missing:
            raise HTTPException(status_code=400, detail=f"Unknown user id(s): {', '.join(missing)}")
        # Reject self-sharing
        if owner_user_id and owner_user_id.lower() in {uid.lower() for uid in raw_ids}:
            raise HTTPException(
                status_code=400,
                detail="Cannot share a custom skill with yourself.  Owners already have full access.",
            )
        # Replace atomically
        await share_repo.replace_sharees(
            skill_name=skill_name,
            owner_user_id=owner_user_id,
            sharee_user_ids=set(raw_ids),
        )
        sharees = [
            SkillShareUserInfo(id=uid, email=valid_ids[uid.lower()][0], system_role=valid_ids[uid.lower()][1])
            for uid in raw_ids
        ]
        sharees.sort(key=lambda s: s.email)
        owner_email = valid_ids.get(owner_user_id.lower(), ("", ""))[0] if owner_user_id else ""
        if not owner_email and owner_user_id:
            user = await provider.repository.get_user_by_id(owner_user_id)
            if user is not None:
                owner_email = str(user.email)
        await refresh_skills_system_prompt_cache_async()
        return SkillShareUpdateResponse(
            skill_name=skill_name,
            owner_user_id=owner_user_id,
            owner_email=owner_email,
            sharees=sharees,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update shares for custom skill %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update shares: {str(e)}")


async def _write_custom_skill_support_file(
    *,
    storage,
    skill_name: str,
    path: str,
    content: str,
    config: AppConfig,
    author: str,
) -> str:
    normalized_path = path.replace("\\", "/").lstrip("/")
    executable = normalized_path.startswith("scripts/") or "/scripts/" in f"/{normalized_path}/"
    scan = await scan_skill_content(content, executable=executable, location=f"{skill_name}/{normalized_path}", app_config=config)
    if scan.decision == "block":
        raise HTTPException(status_code=400, detail=f"Security scan blocked the write: {scan.reason}")
    if executable and scan.decision != "allow":
        raise HTTPException(status_code=400, detail=f"Security scan rejected executable content: {scan.reason}")
    target = storage.ensure_safe_support_path(skill_name, normalized_path)
    prev_content = target.read_text(encoding="utf-8") if target.exists() else None
    storage.write_custom_skill(skill_name, normalized_path, content)
    storage.append_history(
        skill_name,
        {
            "action": "human_write_file",
            "author": author,
            "thread_id": None,
            "file_path": normalized_path,
            "prev_content": prev_content,
            "new_content": content,
            "scanner": {"decision": scan.decision, "reason": scan.reason},
        },
    )
    return normalized_path


@router.get("/skills/custom/{skill_name}/file", response_model=CustomSkillFileContentResponse, summary="Read Custom Skill File")
async def read_custom_skill_file(skill_name: str, path: str, config: AppConfig = Depends(get_config)) -> CustomSkillFileContentResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        storage = get_or_new_skill_storage(app_config=config)
        content = storage.read_custom_skill_file(skill_name, path)
        normalized_path = path.replace("\\", "/").lstrip("/")
        return CustomSkillFileContentResponse(path=normalized_path, content=content)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to read file %s for custom skill %s: %s", path, skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to read custom skill file: {str(e)}")


@router.put("/skills/custom/{skill_name}/file", response_model=CustomSkillFileContentResponse, summary="Write Custom Skill File")
async def write_custom_skill_file(
    skill_name: str,
    request: CustomSkillFileWriteRequest,
    config: AppConfig = Depends(get_config),
) -> CustomSkillFileContentResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        storage = get_or_new_skill_storage(app_config=config)
        storage.ensure_custom_skill_is_editable(skill_name)
        normalized_path = await _write_custom_skill_support_file(
            storage=storage,
            skill_name=skill_name,
            path=request.path,
            content=request.content,
            config=config,
            author="human",
        )
        await refresh_skills_system_prompt_cache_async()
        return CustomSkillFileContentResponse(path=normalized_path, content=request.content)
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to write file %s for custom skill %s: %s", request.path, skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to write custom skill file: {str(e)}")


@router.delete("/skills/custom/{skill_name}/file", summary="Delete Custom Skill File")
async def delete_custom_skill_file(skill_name: str, path: str, config: AppConfig = Depends(get_config)) -> dict[str, bool]:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        storage = get_or_new_skill_storage(app_config=config)
        storage.ensure_custom_skill_is_editable(skill_name)
        normalized_path = path.replace("\\", "/").strip("/")
        prev_content = storage.delete_custom_skill_file(skill_name, normalized_path)
        storage.append_history(
            skill_name,
            {
                "action": "human_delete_file",
                "author": "human",
                "thread_id": None,
                "file_path": normalized_path,
                "prev_content": prev_content,
                "new_content": None,
                "scanner": {"decision": "allow", "reason": "File deletion requested."},
            },
        )
        await refresh_skills_system_prompt_cache_async()
        return {"success": True}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to delete file %s for custom skill %s: %s", path, skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete custom skill file: {str(e)}")


@router.post("/skills/custom/{skill_name}/directories", response_model=CustomSkillFileEntry, summary="Create Custom Skill Directory")
async def create_custom_skill_directory(
    skill_name: str,
    request: CustomSkillDirectoryCreateRequest,
    config: AppConfig = Depends(get_config),
) -> CustomSkillFileEntry:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        storage = get_or_new_skill_storage(app_config=config)
        normalized_path = request.path.replace("\\", "/").strip("/")
        storage.mkdir_custom_skill_directory(skill_name, normalized_path)
        storage.append_history(
            skill_name,
            {
                "action": "human_mkdir",
                "author": "human",
                "thread_id": None,
                "file_path": normalized_path,
                "prev_content": None,
                "new_content": None,
                "scanner": {"decision": "allow", "reason": "Directory creation requested."},
            },
        )
        return CustomSkillFileEntry(path=normalized_path, type="directory", size=None)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to create directory %s for custom skill %s: %s", request.path, skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create custom skill directory: {str(e)}")


@router.post("/skills/custom/{skill_name}/upload", response_model=CustomSkillUploadResponse, summary="Upload Custom Skill Files")
async def upload_custom_skill_files(
    skill_name: str,
    files: list[UploadFile] = File(...),
    paths: list[str] = Form(...),
    config: AppConfig = Depends(get_config),
) -> CustomSkillUploadResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        if len(files) != len(paths):
            raise HTTPException(status_code=400, detail="Every uploaded file must include a matching path.")
        storage = get_or_new_skill_storage(app_config=config)
        storage.ensure_custom_skill_is_editable(skill_name)
        uploaded_paths: list[str] = []
        for upload, relative_path in zip(files, paths, strict=True):
            data = await upload.read()
            normalized_path = relative_path.replace("\\", "/").lstrip("/")
            if not normalized_path or normalized_path.endswith("/"):
                raise HTTPException(status_code=400, detail="Uploaded file paths must include a filename.")
            try:
                text_content = data.decode("utf-8")
            except UnicodeDecodeError:
                storage.write_custom_skill_bytes(skill_name, normalized_path, data)
                storage.append_history(
                    skill_name,
                    {
                        "action": "human_upload_file",
                        "author": "human",
                        "thread_id": None,
                        "file_path": normalized_path,
                        "prev_content": None,
                        "new_content": None,
                        "scanner": {"decision": "allow", "reason": "Binary upload."},
                    },
                )
            else:
                await _write_custom_skill_support_file(
                    storage=storage,
                    skill_name=skill_name,
                    path=normalized_path,
                    content=text_content,
                    config=config,
                    author="human",
                )
            uploaded_paths.append(normalized_path)
        await refresh_skills_system_prompt_cache_async()
        return CustomSkillUploadResponse(paths=uploaded_paths)
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to upload files for custom skill %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to upload custom skill files: {str(e)}")


@router.put("/skills/custom/{skill_name}", response_model=CustomSkillContentResponse, summary="Edit Custom Skill")
async def update_custom_skill(skill_name: str, request: CustomSkillUpdateRequest, config: AppConfig = Depends(get_config)) -> CustomSkillContentResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        storage = get_or_new_skill_storage(app_config=config)
        storage.ensure_custom_skill_is_editable(skill_name)
        storage.validate_skill_markdown_content(skill_name, request.content)
        scan = await scan_skill_content(request.content, executable=False, location=f"{skill_name}/{SKILL_MD_FILE}", app_config=config)
        if scan.decision == "block":
            raise HTTPException(status_code=400, detail=f"Security scan blocked the edit: {scan.reason}")
        prev_content = storage.read_custom_skill(skill_name) if storage.custom_skill_exists(skill_name) else None
        storage.write_custom_skill(skill_name, SKILL_MD_FILE, request.content)
        storage.append_history(
            skill_name,
            {
                "action": "human_edit",
                "author": "human",
                "thread_id": None,
                "file_path": SKILL_MD_FILE,
                "prev_content": prev_content,
                "new_content": request.content,
                "scanner": {"decision": scan.decision, "reason": scan.reason},
            },
        )
        await refresh_skills_system_prompt_cache_async()
        return await get_custom_skill(skill_name, config)
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to update custom skill %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update custom skill: {str(e)}")


@router.delete("/skills/custom/{skill_name}", summary="Delete Custom Skill")
async def delete_custom_skill(skill_name: str, config: AppConfig = Depends(get_config)) -> dict[str, bool]:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        storage = get_or_new_skill_storage(app_config=config)
        storage.delete_custom_skill(
            skill_name,
            history_meta={
                "action": "human_delete",
                "author": "human",
                "thread_id": None,
                "file_path": SKILL_MD_FILE,
                "prev_content": None,
                "new_content": None,
                "scanner": {"decision": "allow", "reason": "Deletion requested."},
            },
        )
        await refresh_skills_system_prompt_cache_async()
        return {"success": True}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to delete custom skill %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete custom skill: {str(e)}")


@router.get("/skills/custom/{skill_name}/history", response_model=CustomSkillHistoryResponse, summary="Get Custom Skill History")
async def get_custom_skill_history(skill_name: str, config: AppConfig = Depends(get_config)) -> CustomSkillHistoryResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        storage = get_or_new_skill_storage(app_config=config)
        if not storage.custom_skill_exists(skill_name) and not storage.get_skill_history_file(skill_name).exists():
            raise HTTPException(status_code=404, detail=f"Custom skill '{skill_name}' not found")
        return CustomSkillHistoryResponse(history=storage.read_history(skill_name))
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Failed to read history for %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to read history: {str(e)}")


@router.get("/skills/custom/{skill_name}/versions", response_model=CustomSkillVersionsResponse, summary="List Custom Skill Versions")
async def list_custom_skill_versions(skill_name: str, config: AppConfig = Depends(get_config)) -> CustomSkillVersionsResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        storage = get_or_new_skill_storage(app_config=config)
        if not storage.custom_skill_exists(skill_name) and not storage.get_skill_versions_dir(skill_name).exists():
            raise HTTPException(status_code=404, detail=f"Custom skill '{skill_name}' not found")
        return CustomSkillVersionsResponse(versions=storage.list_skill_versions(skill_name))
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Failed to list versions for %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list versions: {str(e)}")


@router.post("/skills/custom/{skill_name}/versions", response_model=dict, summary="Create Custom Skill Version Snapshot")
async def create_custom_skill_version_snapshot(
    skill_name: str,
    request: CustomSkillVersionCreateRequest,
    config: AppConfig = Depends(get_config),
) -> dict:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        storage = get_or_new_skill_storage(app_config=config)
        storage.ensure_custom_skill_is_editable(skill_name)
        record = storage.create_skill_version(
            skill_name,
            action=request.action,
            author="human",
            message=request.message,
            thread_id=request.thread_id,
        )
        return record
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to create version snapshot for %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create version snapshot: {str(e)}")


@router.get("/skills/custom/{skill_name}/versions/{seq}/files", response_model=CustomSkillFilesResponse, summary="List Custom Skill Version Files")
async def list_custom_skill_version_files(skill_name: str, seq: int, config: AppConfig = Depends(get_config)) -> CustomSkillFilesResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        storage = get_or_new_skill_storage(app_config=config)
        files = storage.list_skill_version_files(skill_name, seq)
        return CustomSkillFilesResponse(files=[CustomSkillFileEntry(**entry) for entry in files])
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to list files for %s version %s: %s", skill_name, seq, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list version files: {str(e)}")


@router.get("/skills/custom/{skill_name}/versions/{seq}/file", response_model=CustomSkillFileContentResponse, summary="Read Custom Skill Version File")
async def read_custom_skill_version_file(
    skill_name: str,
    seq: int,
    path: str,
    config: AppConfig = Depends(get_config),
) -> CustomSkillFileContentResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        storage = get_or_new_skill_storage(app_config=config)
        content = storage.read_skill_version_file(skill_name, seq, path)
        normalized_path = path.replace("\\", "/").lstrip("/")
        return CustomSkillFileContentResponse(path=normalized_path, content=content)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to read version file %s for %s@%s: %s", path, skill_name, seq, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to read version file: {str(e)}")


@router.post("/skills/custom/{skill_name}/versions/{seq}/restore", response_model=CustomSkillVersionRestoreResponse, summary="Restore Custom Skill Version")
async def restore_custom_skill_version(skill_name: str, seq: int, config: AppConfig = Depends(get_config)) -> CustomSkillVersionRestoreResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        storage = get_or_new_skill_storage(app_config=config)
        record = storage.restore_skill_version(skill_name, seq, author="human", thread_id=None)
        await refresh_skills_system_prompt_cache_async()
        return CustomSkillVersionRestoreResponse(version=record)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to restore %s to version %s: %s", skill_name, seq, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to restore version: {str(e)}")


@router.post("/skills/custom/{skill_name}/rollback", response_model=CustomSkillContentResponse, summary="Rollback Custom Skill")
async def rollback_custom_skill(skill_name: str, request: SkillRollbackRequest, config: AppConfig = Depends(get_config)) -> CustomSkillContentResponse:
    try:
        storage = get_or_new_skill_storage(app_config=config)
        if not storage.custom_skill_exists(skill_name) and not storage.get_skill_history_file(skill_name).exists():
            raise HTTPException(status_code=404, detail=f"Custom skill '{skill_name}' not found")
        history = storage.read_history(skill_name)
        if not history:
            raise HTTPException(status_code=400, detail=f"Custom skill '{skill_name}' has no history")
        record = history[request.history_index]
        target_content = record.get("prev_content")
        if target_content is None:
            raise HTTPException(status_code=400, detail="Selected history entry has no previous content to roll back to")
        storage.validate_skill_markdown_content(skill_name, target_content)
        scan = await scan_skill_content(target_content, executable=False, location=f"{skill_name}/{SKILL_MD_FILE}", app_config=config)
        skill_file = storage.get_custom_skill_file(skill_name)
        current_content = skill_file.read_text(encoding="utf-8") if skill_file.exists() else None
        history_entry = {
            "action": "rollback",
            "author": "human",
            "thread_id": None,
            "file_path": SKILL_MD_FILE,
            "prev_content": current_content,
            "new_content": target_content,
            "rollback_from_ts": record.get("ts"),
            "scanner": {"decision": scan.decision, "reason": scan.reason},
        }
        if scan.decision == "block":
            storage.append_history(skill_name, history_entry)
            raise HTTPException(status_code=400, detail=f"Rollback blocked by security scanner: {scan.reason}")
        storage.write_custom_skill(skill_name, SKILL_MD_FILE, target_content)
        storage.append_history(skill_name, history_entry)
        await refresh_skills_system_prompt_cache_async()
        return await get_custom_skill(skill_name, config)
    except HTTPException:
        raise
    except IndexError:
        raise HTTPException(status_code=400, detail="history_index is out of range")
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to roll back custom skill %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to roll back custom skill: {str(e)}")


@router.get(
    "/skills/{skill_name}",
    response_model=SkillResponse,
    summary="Get Skill Details",
    description="Retrieve detailed information about a specific skill by its name. Disabled public skills are hidden from non-admins.",
)
async def get_skill(skill_name: str, request: Request, config: AppConfig = Depends(get_config)) -> SkillResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        auth = await _resolve_auth(request)
        skills = get_or_new_skill_storage(app_config=config).load_skills(enabled_only=False)
        skill = next((s for s in skills if s.name == skill_name), None)

        if skill is None:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

        visible = _skills_visible_to_caller([skill], is_admin=_is_admin_auth(auth))
        if not visible:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

        return _skill_to_response(skill)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get skill {skill_name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get skill: {str(e)}")


@router.put(
    "/skills/{skill_name}",
    response_model=SkillResponse,
    summary="Update Skill",
    description="Update a skill's enabled status by modifying the extensions_config.json file. Toggling public skills requires admin access.",
)
async def update_skill(
    skill_name: str,
    body: SkillUpdateRequest,
    http_request: Request,
    config: AppConfig = Depends(get_config),
) -> SkillResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        skills = get_or_new_skill_storage(app_config=config).load_skills(enabled_only=False)
        skill = next((s for s in skills if s.name == skill_name), None)

        if skill is None:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

        if skill.category == SkillCategory.PUBLIC:
            await _require_system_admin(http_request)

        config_path = ExtensionsConfig.resolve_config_path()
        if config_path is None:
            config_path = Path.cwd().parent / "extensions_config.json"
            logger.info(f"No existing extensions config found. Creating new config at: {config_path}")

        extensions_config = get_extensions_config()
        extensions_config.skills[skill_name] = SkillStateConfig(enabled=body.enabled)

        config_data = {
            "mcpServers": {name: server.model_dump() for name, server in extensions_config.mcp_servers.items()},
            "skills": {name: {"enabled": skill_config.enabled} for name, skill_config in extensions_config.skills.items()},
        }

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)

        logger.info(f"Skills configuration updated and saved to: {config_path}")
        reload_extensions_config()
        await refresh_skills_system_prompt_cache_async()

        skills = get_or_new_skill_storage(app_config=config).load_skills(enabled_only=False)
        updated_skill = next((s for s in skills if s.name == skill_name), None)

        if updated_skill is None:
            raise HTTPException(status_code=500, detail=f"Failed to reload skill '{skill_name}' after update")

        logger.info(f"Skill '{skill_name}' enabled status updated to {body.enabled}")
        return _skill_to_response(updated_skill)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update skill {skill_name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update skill: {str(e)}")
