"""Neutral contracts shared by channel adapters and lifecycle services."""

from __future__ import annotations

from typing import Protocol


class EventDeduplicator(Protocol):
    """Durably claim a provider event before binding execution."""

    async def claim(
        self,
        binding_id: str,
        event_id: str,
        *,
        system_scope: object,
    ) -> bool:
        """Atomically claim an event under trusted system authority.

        Args:
            binding_id: Persisted provider binding that owns the event scope.
            event_id: Provider-stable replay identifier.
            system_scope: Unforgeable application authority required to claim.

        Returns:
            ``True`` only for the first successful claim; ``False`` for a
            replay that was already claimed.

        Raises:
            PermissionError: ``system_scope`` is not trusted authority.
            ValueError: A required identifier is empty.
            RuntimeError: ``binding_id`` is not a persisted Feishu binding.
        """
        ...
