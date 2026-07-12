"""Compose an agent's runtime instructions from its AGENT.md and SOUL.md.

Per design doc §3.3, the two files are concatenated in a fixed order —
``AGENT.md`` first (responsibilities, workflow, boundaries), then ``SOUL.md``
(persona, tone, values) — and each non-empty file is wrapped in a labelled
prompt block so the model can tell them apart. Whitespace-only content counts
as empty. At least one of the two must be non-empty, enforced both here and in
publish validation.
"""

from __future__ import annotations


def compose_agent_instructions(agent_markdown: str, soul_markdown: str) -> str:
    """Concatenate AGENT.md then SOUL.md into labelled prompt blocks.

    Empty (or whitespace-only) files are skipped. Raises ``ValueError`` if both
    are empty — a publishable agent must describe itself in at least one file.
    """
    blocks: list[str] = []
    agent_body = agent_markdown.strip()
    soul_body = soul_markdown.strip()
    if agent_body:
        blocks.append(f"<agent_instructions>\n{agent_body}\n</agent_instructions>")
    if soul_body:
        blocks.append(f"<agent_soul>\n{soul_body}\n</agent_soul>")
    if not blocks:
        raise ValueError("at least one of AGENT.md / SOUL.md must be non-empty")
    return "\n\n".join(blocks)
