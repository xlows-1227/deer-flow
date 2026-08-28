"""External Skills API V1 — for third-party system integrations.

No authentication required. The ``psid`` query parameter identifies the caller's
account scope and controls visibility of custom skills. Public skills are always
returned regardless of psid.

The ``psid`` supports multiple identifier formats:
- Full email: "jialong.wang@yumchina.com"
- Email prefix: "jialong.wang"  (auto-resolved via fuzzy match)
- SSO/LDAP account: "wcj8902"  (resolved via oauth_id lookup)

We resolve the psid to the internal user UUID and then match against the
custom skill owner metadata stored in ``.owners/*.json``.

All endpoints require a ``sign`` parameter — a Base64-encoded token whose
plaintext is ``psid|timestamp|SKILL_API_SIGN_KEY``. The gateway decodes it
and verifies that:

1. The psid inside the sign matches the psid query parameter.
2. The key inside the sign matches the server's SKILL_API_SIGN_KEY.
3. The request is within 30 minutes of the embedded timestamp.

Use ``app.gateway.skill_sign.generate_sign(psid)`` to generate the sign on the
caller side (requires the shared ``SKILL_API_SIGN_KEY`` environment variable).

Interfaces:
- GET /api/v1/skills?psid=xxx&sign=xxx
    List all public skills + custom skills owned by psid.
- GET /api/v1/skills/{skill_id}?psid=xxx&sign=xxx
    Skill detail + version list + download URLs.
- GET /api/v1/skills/{skill_id}/download?psid=xxx&sign=xxx&version=N
    Download a specific version of the skill as .skill (ZIP).
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.gateway.skill_sign import verify_sign
from deerflow.skills import Skill
from deerflow.skills.storage.local_skill_storage import LocalSkillStorage
from deerflow.skills.types import SkillCategory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["skills-external"])


def _get_storage_no_isolation():
    """Get a SkillStorage instance with owner isolation disabled.

    This allows loading ALL skills (public + custom) without filtering by
    the current user's owner. We then do our own owner check using the
    resolved user UUID.
    """
    return LocalSkillStorage(enforce_owner_isolation=False)


def _require_psid(psid: str | None) -> str:
    if not psid:
        raise HTTPException(
            status_code=400,
            detail={"code": "missing_psid", "message": "The 'psid' query parameter is required."},
        )
    return psid.strip()


def _verify_sign(psid: str, sign: str | None) -> None:
    """Validate the sign parameter: decrypt, check psid match, check expiry."""
    if not sign:
        raise HTTPException(
            status_code=401,
            detail={"code": "missing_sign", "message": "The 'sign' query parameter is required."},
        )
    try:
        verify_sign(sign, psid)
    except ValueError as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": "invalid_sign", "message": str(exc)},
        ) from exc


async def _resolve_user_uuid(psid: str) -> str | None:
    """Resolve psid (email, oauth_id, or account identifier) to internal user UUID.

    Tries multiple lookup strategies in order:
    1. Exact email match (e.g. "jialong.wang@yumchina.com")
    2. Email prefix match (the part before '@', e.g. "jialong.wang")
    3. oauth_id match — lookup by LDAP/SSO account name (e.g. "wcj8902")

    Returns the user's UUID string, or None if the user is not found.
    """
    try:
        from app.gateway.deps import get_local_provider

        provider = get_local_provider()

        # 1. Exact email match
        user = await provider.get_user_by_email(psid)
        if user is not None:
            return str(user.id)

        # 2. Email prefix match: extract the part before '@' from psid
        if "@" in psid:
            prefix = psid.split("@")[0].lower()
            from deerflow.persistence.engine import get_session_factory

            sf = get_session_factory()
            if sf is not None:
                import sqlalchemy as sa
                from deerflow.persistence.user.model import UserRow

                async with sf() as session:
                    stmt = sa.select(UserRow).where(
                        sa.func.lower(UserRow.email).like(f"{prefix}%")
                    )
                    result = await session.execute(stmt)
                    row = result.scalar_one_or_none()
                    if row is not None:
                        logger.info(
                            "Resolved psid '%s' to user '%s' via email prefix match",
                            psid,
                            row.email,
                        )
                        return row.id

        # 3. oauth_id match: treat psid as an LDAP/SSO account identifier
        #    (e.g. "wcj8902") and look it up in the oauth_id column.
        #    This supports deployments where users log in with their SSO
        #    account name instead of full email.
        oauth_user = await provider.get_user_by_oauth("ldap", psid)
        if oauth_user is not None:
            logger.info(
                "Resolved psid '%s' to user '%s' via oauth_id match",
                psid,
                oauth_user.email,
            )
            return str(oauth_user.id)

        logger.warning("No user found for psid '%s' via any lookup strategy", psid)
    except Exception:
        logger.warning("Failed to resolve psid '%s' to user UUID", psid, exc_info=True)
    return None


async def _require_existing_user(psid: str) -> str:
    """Resolve psid to user UUID or raise 401 if account does not exist."""
    user_uuid = await _resolve_user_uuid(psid)
    if user_uuid is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "account_not_found",
                "message": f"No account found for psid '{psid}'. The psid must match an existing user in the system.",
            },
        )
    return user_uuid


def _read_custom_skill_owner_id(skill_dir: Path) -> str | None:
    """Read the owner UUID from a custom skill's owner metadata file.

    The owner file is stored at:
        <skills_root>/custom/.owners/<skill_name>.json
    with format: {"owner_id": "<uuid>"}

    Also checks the legacy location:
        <skill_dir>/.owner.json
    """
    owner_file = skill_dir.parent / ".owners" / f"{skill_dir.name}.json"
    if not owner_file.exists():
        # Legacy location: <skill_dir>/.owner.json
        legacy_file = skill_dir / ".owner.json"
        if legacy_file.exists():
            owner_file = legacy_file
        else:
            return None
    try:
        payload = json.loads(owner_file.read_text(encoding="utf-8"))
        owner_id = payload.get("owner_id") if isinstance(payload, dict) else None
        return str(owner_id).strip() if owner_id else None
    except (OSError, json.JSONDecodeError):
        logger.warning("Invalid custom skill owner metadata: %s", owner_file, exc_info=True)
        return None


async def _get_shared_skill_names(user_uuid: str) -> set[str]:
    """Return skill names shared with the given user via skill_shares table."""
    try:
        from deerflow.persistence.engine import get_session_factory

        sf = get_session_factory()
        if sf is None:
            logger.warning("_get_shared_skill_names: no session factory available")
            return set()
        from deerflow.persistence.skill_share.store import SkillShareRepository

        repo = SkillShareRepository(sf)
        # Try exact match first
        names = await repo.list_skill_names_shared_with_user(user_uuid)
        if not names and user_uuid != user_uuid.lower():
            # Fallback: try lowercased user_id
            names = await repo.list_skill_names_shared_with_user(user_uuid.lower())
        logger.info(
            "_get_shared_skill_names: user_uuid=%s, shared_skill_names=%s",
            user_uuid,
            names,
        )
        return names
    except Exception:
        logger.warning("Failed to query shared skills for user %s", user_uuid, exc_info=True)
        return set()


async def _filter_skills_for_psid(psid: str) -> list[Skill]:
    user_uuid = await _resolve_user_uuid(psid)
    storage = _get_storage_no_isolation()
    all_skills = storage.load_skills(enabled_only=True)

    # Fetch skill names shared with this user
    shared_names: set[str] = set()
    if user_uuid:
        shared_names = await _get_shared_skill_names(user_uuid)

    visible: list[Skill] = []
    for skill in all_skills:
        if skill.category == SkillCategory.PUBLIC:
            visible.append(skill)
        elif skill.category == SkillCategory.CUSTOM:
            owner_id = _read_custom_skill_owner_id(skill.skill_dir)
            if owner_id and user_uuid and owner_id == user_uuid:
                visible.append(skill)
            elif skill.name in shared_names:
                visible.append(skill)
    return visible


async def _skill_accessible_to_psid(skill_id: str, psid: str) -> Skill | None:
    user_uuid = await _resolve_user_uuid(psid)
    storage = _get_storage_no_isolation()
    all_skills = storage.load_skills(enabled_only=True)
    skill = next((s for s in all_skills if s.name == skill_id), None)
    if skill is None:
        return None
    if skill.category == SkillCategory.PUBLIC:
        return skill
    # Custom skill: check ownership
    owner_id = _read_custom_skill_owner_id(skill.skill_dir)
    if owner_id and user_uuid and owner_id == user_uuid:
        return skill
    # Check if shared with this user
    if user_uuid:
        shared_names = await _get_shared_skill_names(user_uuid)
        if skill_id in shared_names:
            return skill
    return None


async def _is_skill_owned_by_psid(skill_id: str, psid: str) -> bool:
    """Check if the psid user is the OWNER of the skill (not just a sharee)."""
    user_uuid = await _resolve_user_uuid(psid)
    if not user_uuid:
        return False
    storage = _get_storage_no_isolation()
    all_skills = storage.load_skills(enabled_only=True)
    skill = next((s for s in all_skills if s.name == skill_id), None)
    if skill is None or skill.category != SkillCategory.CUSTOM:
        return False
    owner_id = _read_custom_skill_owner_id(skill.skill_dir)
    return bool(owner_id and owner_id == user_uuid)


def _get_request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ExternalSkillSummary(BaseModel):
    """Basic skill info returned by the list endpoint."""

    skill_id: str = Field(..., description="Unique skill identifier (skill name)")
    name: str = Field(..., description="Skill name")
    description: str = Field(default="", description="Skill description")
    display_name: str | None = Field(None, description="Display name")
    description_zh: str | None = Field(None, description="Chinese description")
    category: str = Field(..., description="'public' or 'custom'")
    version: str | None = Field(None, description="Latest version label (custom only)")
    shared: bool = Field(default=False, description="True if this skill is shared with (not owned by) the caller")


class ExternalSkillsListResponse(BaseModel):
    """Response for GET /api/v1/skills."""

    request_id: str | None = None
    total: int
    skills: list[ExternalSkillSummary]


class SkillVersionInfo(BaseModel):
    """Version metadata for a skill."""

    seq: int = Field(..., description="Version sequence number")
    label: str | None = Field(None, description="Version label (e.g. v2)")
    action: str | None = Field(None, description="Action that created this version")
    message: str | None = Field(None, description="User note for this version")
    ts: str | None = Field(None, description="Timestamp of version creation")
    download_url: str | None = Field(None, description="Download URL for this version")


class ExternalSkillDetailResponse(BaseModel):
    """Response for GET /api/v1/skills/{skill_id}."""

    request_id: str | None = None
    skill: ExternalSkillSummary
    versions: list[SkillVersionInfo] = Field(default_factory=list, description="Version list (custom only)")


# ---------------------------------------------------------------------------
# Helper: ZIP building
# ---------------------------------------------------------------------------


def _collect_skill_files(skill_dir: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for path in sorted(skill_dir.rglob("*")):
        if path.is_file() and not any(part.startswith(".") for part in path.relative_to(skill_dir).parts):
            arcname = str(path.relative_to(skill_dir))
            files.append((arcname, path))
    return files


def _build_skill_zip(skill_dir: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, filepath in _collect_skill_files(skill_dir):
            zf.write(filepath, arcname)
    return buf.getvalue()


def _build_version_zip(skill_name: str, seq: int) -> bytes:
    storage = _get_storage_no_isolation()
    version_dir = storage.get_skill_versions_dir(skill_name) / str(seq)
    if not version_dir.exists() or not version_dir.is_dir():
        raise FileNotFoundError(f"Version {seq} not found for skill '{skill_name}'")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, filepath in _collect_skill_files(version_dir):
            zf.write(filepath, arcname)
    return buf.getvalue()


def _get_skill_dir(skill: Skill) -> Path:
    storage = _get_storage_no_isolation()
    if skill.category == SkillCategory.PUBLIC:
        return storage.get_skills_root_path() / SkillCategory.PUBLIC.value / skill.name
    return storage.get_custom_skill_dir(skill.name)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/skills",
    response_model=ExternalSkillsListResponse,
    summary="List Skills",
    description="Returns all public skills plus custom skills owned by or shared with the given psid. A valid sign is required.",
)
async def list_skills(
    request: Request,
    psid: str = Query(..., description="User PSID (email) for filtering custom skills"),
    sign: str = Query(..., description="Encrypted sign: Fernet(psid|timestamp)"),
) -> ExternalSkillsListResponse:
    psid = _require_psid(psid)
    _verify_sign(psid, sign)
    user_uuid = await _require_existing_user(psid)
    skills = await _filter_skills_for_psid(psid)

    # Determine shared skill names for marking
    shared_names: set[str] = set()
    if user_uuid:
        shared_names = await _get_shared_skill_names(user_uuid)

    summaries: list[ExternalSkillSummary] = []
    for skill in skills:
        version = None
        if skill.category == SkillCategory.CUSTOM:
            try:
                storage = _get_storage_no_isolation()
                versions = storage.list_skill_versions(skill.name)
                if versions:
                    version = versions[0].get("label") or f"v{versions[0].get('seq')}"
            except Exception:
                pass
        is_shared = skill.category == SkillCategory.CUSTOM and skill.name in shared_names
        summaries.append(
            ExternalSkillSummary(
                skill_id=skill.name,
                name=skill.name,
                description=skill.description,
                display_name=skill.display_name,
                description_zh=skill.description_zh,
                category=skill.category.value,
                version=version,
                shared=is_shared,
            )
        )

    return ExternalSkillsListResponse(
        request_id=_get_request_id(request),
        total=len(summaries),
        skills=summaries,
    )


@router.get(
    "/skills/{skill_id}",
    response_model=ExternalSkillDetailResponse,
    summary="Get Skill Detail",
    description="Get skill information including version list and per-version download URLs. A valid sign is required.",
)
async def get_skill_detail(
    skill_id: str,
    request: Request,
    psid: str = Query(..., description="User PSID (email) for access control"),
    sign: str = Query(..., description="Encrypted sign: Fernet(psid|timestamp)"),
) -> ExternalSkillDetailResponse:
    psid = _require_psid(psid)
    _verify_sign(psid, sign)
    user_uuid = await _require_existing_user(psid)
    skill = await _skill_accessible_to_psid(skill_id, psid)
    if skill is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "skill_not_found", "message": f"Skill '{skill_id}' not found or not accessible for psid '{psid}'"},
        )

    version_label = None
    versions: list[SkillVersionInfo] = []

    if skill.category == SkillCategory.CUSTOM:
        try:
            storage = _get_storage_no_isolation()
            raw_versions = storage.list_skill_versions(skill.name)
            for v in raw_versions:
                seq = v.get("seq", 0)
                label = v.get("label") or f"v{seq}"
                download_url = f"/api/v1/skills/{skill.name}/download?psid={psid}&sign={sign}&version={seq}"
                versions.append(
                    SkillVersionInfo(
                        seq=seq,
                        label=label,
                        action=v.get("action"),
                        message=v.get("message"),
                        ts=v.get("ts"),
                        download_url=download_url,
                    )
                )
            if raw_versions:
                version_label = raw_versions[0].get("label") or f"v{raw_versions[0].get('seq')}"
        except Exception:
            logger.warning("Failed to list versions for skill %s", skill.name, exc_info=True)

    summary = ExternalSkillSummary(
        skill_id=skill.name,
        name=skill.name,
        description=skill.description,
        display_name=skill.display_name,
        description_zh=skill.description_zh,
        category=skill.category.value,
        version=version_label,
    )

    return ExternalSkillDetailResponse(
        request_id=_get_request_id(request),
        skill=summary,
        versions=versions,
    )


@router.get(
    "/skills/{skill_id}/download",
    summary="Download Skill",
    description="Download a skill as a .skill (ZIP) file. For custom skills, specify version parameter. A valid sign is required.",
)
async def download_skill(
    skill_id: str,
    request: Request,
    psid: str = Query(..., description="User PSID (email) for access control"),
    sign: str = Query(..., description="Encrypted sign: Fernet(psid|timestamp)"),
    version: int | None = Query(None, description="Version sequence number (required for custom skills)"),
):
    psid = _require_psid(psid)
    _verify_sign(psid, sign)
    user_uuid = await _require_existing_user(psid)
    skill = await _skill_accessible_to_psid(skill_id, psid)
    if skill is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "skill_not_found", "message": f"Skill '{skill_id}' not found or not accessible for psid '{psid}'"},
        )

    # Only custom skills can be downloaded via the external API.
    # Public skills ship with the product and must not be downloadable by third parties.
    if skill.category != SkillCategory.CUSTOM:
        raise HTTPException(
            status_code=403,
            detail={"code": "public_skill_not_downloadable", "message": f"Public skill '{skill_id}' cannot be downloaded. Only custom skills support download."},
        )
    if version is None:
        # For backward compatibility, fall back to the live directory when no
        # version is specified.  Versioned snapshots are preferred.
        skill_dir = _get_skill_dir(skill)
        if not skill_dir.exists():
            raise HTTPException(
                status_code=404,
                detail={"code": "skill_not_found", "message": f"Skill directory for '{skill_id}' not found"},
            )
        zip_bytes = _build_skill_zip(skill_dir)
        filename = f"{skill.name}.zip"
    else:
        try:
            zip_bytes = _build_version_zip(skill.name, version)
        except FileNotFoundError:
            raise HTTPException(
                status_code=404,
                detail={"code": "version_not_found", "message": f"Version {version} for skill '{skill_id}' not found"},
            )
        filename = f"{skill.name}-v{version}.zip"

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Request-Id": _get_request_id(request) or "",
    }
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers=headers,
    )
