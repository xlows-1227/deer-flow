import json
import logging
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.gateway.authz import require_admin
from app.gateway.deps import get_config
from app.gateway.path_utils import resolve_thread_virtual_path
from deerflow.agents.lead_agent.prompt import refresh_skills_system_prompt_cache_async
from deerflow.config.app_config import AppConfig
from deerflow.config.extensions_config import ExtensionsConfig, SkillStateConfig, get_extensions_config, reload_extensions_config
from deerflow.models import create_chat_model
from deerflow.skills import Skill
from deerflow.skills.installer import SkillAlreadyExistsError, SkillSecurityScanError
from deerflow.skills.security_scanner import scan_skill_content
from deerflow.skills.storage import get_or_new_skill_storage
from deerflow.skills.types import SKILL_MD_FILE, SkillCategory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["skills"])


class SkillResponse(BaseModel):
    """Response model for skill information."""

    name: str = Field(..., description="Name of the skill")
    description: str = Field(..., description="Description of what the skill does")
    display_name: str | None = Field(None, description="Display name of the skill (e.g. Chinese name)")
    description_zh: str | None = Field(None, description="Chinese description of the skill")
    license: str | None = Field(None, description="License information")
    category: SkillCategory = Field(..., description="Category of the skill (public or custom)")
    enabled: bool = Field(default=True, description="Whether this skill is enabled")


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


def _skill_to_response(skill: Skill) -> SkillResponse:
    """Convert a Skill object to a SkillResponse."""
    return SkillResponse(
        name=skill.name,
        description=skill.description,
        display_name=skill.display_name,
        description_zh=skill.description_zh,
        license=skill.license,
        category=skill.category,
        enabled=skill.enabled,
    )


@router.get(
    "/skills",
    response_model=SkillsListResponse,
    summary="List All Skills",
    description="Retrieve a list of all available skills from both public and custom directories.",
)
async def list_skills(config: AppConfig = Depends(get_config)) -> SkillsListResponse:
    try:
        skills = get_or_new_skill_storage(app_config=config).load_skills(enabled_only=False)
        return SkillsListResponse(skills=[_skill_to_response(skill) for skill in skills])
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
        result = await get_or_new_skill_storage(app_config=config).ainstall_skill_from_archive(skill_file_path)
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
async def list_custom_skills(config: AppConfig = Depends(get_config)) -> SkillsListResponse:
    try:
        skills = [skill for skill in get_or_new_skill_storage(app_config=config).load_skills(enabled_only=False) if skill.category == SkillCategory.CUSTOM]
        return SkillsListResponse(skills=[_skill_to_response(skill) for skill in skills])
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
async def upload_skill_archive(file: UploadFile = File(...), config: AppConfig = Depends(get_config)) -> SkillInstallResponse:
    filename = file.filename or "skill.skill"
    suffix = Path(filename).suffix.lower()
    if suffix not in {".skill", ".zip"}:
        raise HTTPException(status_code=400, detail="File must have .skill or .zip extension")
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            tmp_path = Path(tmp_file.name)
            while chunk := await file.read(1024 * 1024):
                tmp_file.write(chunk)
        try:
            result = await get_or_new_skill_storage(app_config=config).ainstall_skill_from_archive(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        await refresh_skills_system_prompt_cache_async()
        return SkillInstallResponse(**result)
    except SkillAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except (SkillSecurityScanError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to upload skill archive: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to upload skill archive: {str(e)}")
    finally:
        await file.close()


@router.get(
    "/skills/public/{skill_name}",
    response_model=CustomSkillContentResponse,
    summary="Get Public Skill Content (Admin)",
    description="Read SKILL.md for a public skill. Restricted to admin users.",
)
@require_admin
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
async def get_custom_skill(skill_name: str, config: AppConfig = Depends(get_config)) -> CustomSkillContentResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        skills = get_or_new_skill_storage(app_config=config).load_skills(enabled_only=False)
        skill = next((s for s in skills if s.name == skill_name and s.category == SkillCategory.CUSTOM), None)
        if skill is None:
            raise HTTPException(status_code=404, detail=f"Custom skill '{skill_name}' not found")
        return CustomSkillContentResponse(**_skill_to_response(skill).model_dump(), content=get_or_new_skill_storage(app_config=config).read_custom_skill(skill_name))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get custom skill %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get custom skill: {str(e)}")


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
        prev_content = storage.read_custom_skill(skill_name)
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
    except Exception as e:
        logger.error("Failed to read history for %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to read history: {str(e)}")


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
    description="Retrieve detailed information about a specific skill by its name.",
)
async def get_skill(skill_name: str, config: AppConfig = Depends(get_config)) -> SkillResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        skills = get_or_new_skill_storage(app_config=config).load_skills(enabled_only=False)
        skill = next((s for s in skills if s.name == skill_name), None)

        if skill is None:
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
    description="Update a skill's enabled status by modifying the extensions_config.json file.",
)
async def update_skill(skill_name: str, request: SkillUpdateRequest, config: AppConfig = Depends(get_config)) -> SkillResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        skills = get_or_new_skill_storage(app_config=config).load_skills(enabled_only=False)
        skill = next((s for s in skills if s.name == skill_name), None)

        if skill is None:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

        config_path = ExtensionsConfig.resolve_config_path()
        if config_path is None:
            config_path = Path.cwd().parent / "extensions_config.json"
            logger.info(f"No existing extensions config found. Creating new config at: {config_path}")

        extensions_config = get_extensions_config()
        extensions_config.skills[skill_name] = SkillStateConfig(enabled=request.enabled)

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

        logger.info(f"Skill '{skill_name}' enabled status updated to {request.enabled}")
        return _skill_to_response(updated_skill)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update skill {skill_name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update skill: {str(e)}")
