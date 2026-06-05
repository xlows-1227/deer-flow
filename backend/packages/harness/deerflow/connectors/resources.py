from __future__ import annotations

from deerflow.connectors.schemas import ConnectorInstance


_SAFE_CONNECTION_CONFIG_KEYS = ("host", "port", "query_port", "database")


def connector_safe_connection(instance: ConnectorInstance) -> dict:
    """Return non-secret connection metadata useful for model routing.

    Credentials, secret refs, usernames, passwords, and full connection URLs
    intentionally stay out of this shape. Tools resolve secrets server-side.
    """
    return {key: instance.config[key] for key in _SAFE_CONNECTION_CONFIG_KEYS if key in instance.config and instance.config[key] not in (None, "")}


def connector_safe_summary(instance: ConnectorInstance, capabilities: list[str], policy_summary: dict) -> dict:
    return {
        "id": instance.id,
        "name": instance.name,
        "display_name": instance.display_name,
        "type": instance.type,
        "status": instance.status,
        "connection": connector_safe_connection(instance),
        "capabilities": capabilities,
        "policy_summary": policy_summary,
        "health": instance.health,
        "last_tested_at": instance.last_tested_at.isoformat() if instance.last_tested_at else None,
        "last_used_at": instance.last_used_at.isoformat() if instance.last_used_at else None,
    }
