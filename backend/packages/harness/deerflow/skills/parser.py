import logging
import re
from pathlib import Path
from typing import Any

import yaml

from .types import SKILL_MD_FILE, ConnectorRequirement, Skill, SkillCategory

logger = logging.getLogger(__name__)


def parse_skill_frontmatter(content: str, skill_file: Path) -> dict[str, Any]:
    """Parse the leading YAML frontmatter mapping from SKILL.md content.

    Raises ValueError when the leading block is missing, contains invalid YAML,
    or does not decode to a mapping.
    """
    front_matter_match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)", content, re.DOTALL)
    if front_matter_match is None:
        raise ValueError(f"missing YAML frontmatter in {skill_file}")

    try:
        metadata = yaml.safe_load(front_matter_match.group(1))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML frontmatter in {skill_file}") from exc
    if not isinstance(metadata, dict):
        raise ValueError(f"frontmatter in {skill_file} must be a YAML mapping")
    return metadata


def parse_allowed_tools(raw: object, skill_file: Path) -> list[str] | None:
    """Parse the optional allowed-tools frontmatter field.

    Returns None when the field is omitted. Returns a list when the field is a
    YAML sequence of strings, including an empty list for explicit no-tool
    skills. Raises ValueError for malformed values.
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError(f"allowed-tools in {skill_file} must be a list of strings")

    allowed_tools: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"allowed-tools in {skill_file} must contain only strings")
        tool_name = item.strip()
        if not tool_name:
            raise ValueError(f"allowed-tools in {skill_file} cannot contain empty tool names")
        allowed_tools.append(tool_name)
    return allowed_tools


def parse_connector_requirements(raw: object, skill_file: Path) -> list[ConnectorRequirement] | None:
    """Parse optional requires.connectors frontmatter metadata."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"requires in {skill_file} must be a mapping")
    connectors = raw.get("connectors")
    if connectors is None:
        return None
    if not isinstance(connectors, list):
        raise ValueError(f"requires.connectors in {skill_file} must be a list")
    requirements: list[ConnectorRequirement] = []
    for item in connectors:
        if not isinstance(item, dict):
            raise ValueError(f"requires.connectors in {skill_file} must contain mappings")
        capability = item.get("capability")
        if not isinstance(capability, str) or not capability.strip():
            raise ValueError(f"requires.connectors entries in {skill_file} must include a non-empty capability")
        purpose = item.get("purpose")
        if purpose is not None:
            purpose = str(purpose).strip() or None
        requirements.append(ConnectorRequirement(capability=capability.strip(), purpose=purpose))
    return requirements


def parse_skill_file(
    skill_file: Path,
    category: SkillCategory,
    relative_path: Path | None = None,
    *,
    owner_user_id: str | None = None,
) -> Skill | None:
    """Parse a SKILL.md file and extract metadata.

    Args:
        skill_file: Path to the SKILL.md file.
        category: Category of the skill.
        relative_path: Relative path from the category root to the skill
            directory.  Defaults to the skill directory name when omitted.
        owner_user_id: Optional owner identity.  Only populated for
            ``SkillCategory.CUSTOM`` skills where the storage layer has
            already resolved ownership from the on-disk metadata.

    Returns:
        Skill object if parsing succeeds, None otherwise.
    """
    if not skill_file.exists() or skill_file.name != SKILL_MD_FILE:
        return None

    try:
        content = skill_file.read_text(encoding="utf-8")

        try:
            metadata = parse_skill_frontmatter(content, skill_file)
        except ValueError as exc:
            logger.error("Invalid skill frontmatter in %s: %s", skill_file, exc)
            return None

        # Extract required fields.  Both must be non-empty strings.
        name = metadata.get("name")
        description = metadata.get("description")

        if not name or not isinstance(name, str):
            return None
        if not description or not isinstance(description, str):
            return None

        # Normalise: strip surrounding whitespace that YAML may preserve.
        name = name.strip()
        description = description.strip()

        if not name or not description:
            return None

        license_text = metadata.get("license")
        if license_text is not None:
            license_text = str(license_text).strip() or None

        display_name = metadata.get("display_name")
        if display_name is not None:
            display_name = str(display_name).strip() or None

        description_zh = metadata.get("description_zh")
        if description_zh is not None:
            description_zh = str(description_zh).strip() or None

        try:
            allowed_tools = parse_allowed_tools(metadata.get("allowed-tools"), skill_file)
            connector_requirements = parse_connector_requirements(metadata.get("requires"), skill_file)
        except ValueError as exc:
            logger.error("Invalid skill frontmatter in %s: %s", skill_file, exc)
            return None

        return Skill(
            name=name,
            description=description,
            display_name=display_name,
            description_zh=description_zh,
            license=license_text,
            skill_dir=skill_file.parent,
            skill_file=skill_file,
            relative_path=relative_path or Path(skill_file.parent.name),
            category=category,
            allowed_tools=allowed_tools,
            connector_requirements=connector_requirements,
            enabled=True,  # Actual state comes from the extensions config file.
            owner_user_id=owner_user_id,
        )

    except Exception:
        logger.exception("Unexpected error parsing skill file %s", skill_file)
        return None
