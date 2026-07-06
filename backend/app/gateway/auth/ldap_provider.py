"""LDAP authentication provider.

Implements the corporate intranet (Active Directory) login flow:

1. **Search bind** — connect to the directory with a service account and
   locate the user's DN by querying ``<objectclass>=<username>`` under the
   configured base DN. This mirrors the YumChina Spring Boot setup that
   uses ``sAMAccountName`` as the login handle for both regular and
   external employees.
2. **User bind** — re-bind using the located DN together with the password
   the user typed. AD/LDAP considers a successful bind as authentication
   success; a wrong password yields an invalid-credentials bind error.

On success the provider creates (or refreshes) a *shadow* row in the
local ``users`` table so that the rest of the system — JWT issuance,
``get_current_user_from_request``, per-user threading — keeps working with
no awareness of LDAP. Shadow rows store ``password_hash=NULL`` and are
tagged with ``oauth_provider="ldap"`` so the change-password endpoint can
reject them.

All ``ldap3`` calls are synchronous and blocking, so they are wrapped in
``asyncio.to_thread`` to avoid stalling the FastAPI event loop — the same
pattern used by the bcrypt helpers in ``password.py``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.gateway.auth.ldap_config import LdapConfig
from app.gateway.auth.models import User
from app.gateway.auth.providers import AuthProvider
from app.gateway.auth.repositories.base import UserRepository

logger = logging.getLogger(__name__)

# Tag stored in users.oauth_provider to mark LDAP-backed shadow accounts.
LDAP_PROVIDER_TAG = "ldap"


class LdapAuthProvider(AuthProvider):
    """Authenticate users against a corporate LDAP/Active Directory server."""

    def __init__(self, repository: UserRepository, config: LdapConfig) -> None:
        self._repo = repository
        self._config = config

    # ── AuthProvider interface ──────────────────────────────────────────

    async def authenticate(self, credentials: dict) -> User | None:
        """Authenticate ``{"username", "password"}`` against LDAP.

        Returns the local shadow :class:`User` on success, ``None`` on any
        failure (bad password, user not found, server unreachable). We
        intentionally collapse every failure to ``None`` so the router can
        surface a single ``INVALID_CREDENTIALS`` without leaking which side
        failed.
        """
        if not self._config.enabled:
            return None

        username = (credentials.get("username") or "").strip()
        password = credentials.get("password") or ""
        if not username or not password:
            return None

        try:
            result = await asyncio.to_thread(self._bind_and_read, username, password)
        except Exception:  # noqa: BLE001 — boundary: never let LDAP raise out
            logger.warning("LDAP authentication raised for user %r", username, exc_info=True)
            return None

        if result is None:
            return None

        user_dn, attrs = result
        try:
            return await self._find_or_create_shadow_user(username, attrs)
        except Exception:  # noqa: BLE001 — shadow write must not crash login
            logger.warning("Failed to upsert LDAP shadow user for %r", username, exc_info=True)
            return None

    async def get_user(self, user_id: str) -> User | None:
        """Look up a shadow user by local UUID."""
        return await self._repo.get_user_by_id(user_id)

    async def get_user_by_ldap_id(self, username: str) -> User | None:
        """Look up a shadow user by LDAP sAMAccountName."""
        return await self._repo.get_user_by_oauth(LDAP_PROVIDER_TAG, username)

    # ── ldap3 work (runs in a worker thread) ────────────────────────────

    def _bind_and_read(self, username: str, password: str) -> tuple[str, dict[str, Any]] | None:
        """Run the two-stage bind and return ``(user_dn, attrs)`` or ``None``.

        Imports ``ldap3`` lazily so the module can be imported even when the
        package is absent (LDAP disabled). Kept synchronous because
        ``ldap3`` has no native async API.
        """
        from ldap3 import ALL, Connection, Server

        server = Server(self._config.url, get_info=ALL)

        # Stage 1 — service-account bind + search.
        with Connection(
            server,
            user=self._config.bind_username,
            password=self._config.bind_password,
            auto_bind=True,
            read_only=True,
        ) as conn:
            search_filter = f"({self._config.objectclass}={_escape_ldap(username)})"
            attrs_of_interest = [
                self._config.attr_email,
                self._config.attr_realname,
                self._config.attr_sn,
            ]
            found = conn.search(
                search_base=self._config.base,
                search_filter=search_filter,
                attributes=attrs_of_interest,
                size_limit=2,
            )
            if not found or not conn.entries:
                logger.info("LDAP search found no match for %r", username)
                return None
            if len(conn.entries) > 1:
                # Ambiguous identity — do not guess. Operators should fix
                # the directory or narrow the base DN.
                logger.warning("LDAP search returned multiple entries for %r; refusing to guess", username)
                return None
            entry = conn.entries[0]
            user_dn = entry.entry_dn

        # Stage 2 — verify the password by binding as the user.
        try:
            with Connection(server, user=user_dn, password=password, auto_bind=True, read_only=True):
                pass
        except Exception as exc:  # noqa: BLE001 — bind failure = wrong creds
            logger.info("LDAP user bind failed for %r: %s", username, exc)
            return None

        attrs = _extract_attrs(entry, attrs_of_interest)
        return user_dn, attrs

    # ── Shadow-user management ──────────────────────────────────────────

    async def _find_or_create_shadow_user(self, username: str, attrs: dict[str, Any]) -> User:
        """Return the local shadow row for an LDAP user, creating if needed.

        - Existing shadow row → refresh ``email`` from LDAP if it changed
          (a user may rename their mailbox) but fall back to a domain
          address if the new email collides with a non-LDAP account.
        - No shadow row → create one with ``password_hash=None``,
          ``system_role="user"``.
        """
        email = self._pick_email(username, attrs)

        existing = await self._repo.get_user_by_oauth(LDAP_PROVIDER_TAG, username)
        if existing is not None:
            desired = email
            # Only mutate when the LDAP mail actually moved AND is free.
            if desired and desired.lower() != existing.email.lower():
                clash = await self._repo.get_user_by_email(desired)
                if clash is None or str(clash.id) == str(existing.id):
                    existing.email = desired
                    try:
                        existing = await self._repo.update_user(existing)
                    except Exception:  # noqa: BLE001 — refresh is best-effort
                        logger.warning("Shadow email refresh failed for %r", username, exc_info=True)
            return existing

        # Pick a non-colliding email: prefer LDAP mail, fall back to
        # ``<username>@<domain>`` if it is already taken by a local account.
        final_email = email
        if await self._repo.get_user_by_email(final_email) is not None:
            final_email = self._domain_email(username)
            if await self._repo.get_user_by_email(final_email) is not None:
                # Both candidates taken — give up rather than overwrite
                # someone else's row. This is an operator-visible misconfig.
                logger.error(
                    "Cannot create LDAP shadow user for %r: both %s and %s already exist",
                    username, email, final_email,
                )
                raise ValueError(f"Email conflict for LDAP user {username}")

        user = User(
            email=final_email,
            password_hash=None,
            system_role="user",
            oauth_provider=LDAP_PROVIDER_TAG,
            oauth_id=username,
        )
        return await self._repo.create_user(user)

    # ── Helpers ─────────────────────────────────────────────────────────

    def _pick_email(self, username: str, attrs: dict[str, Any]) -> str:
        raw = _first_value(attrs.get(self._config.attr_email))
        if raw:
            return str(raw).strip()
        return self._domain_email(username)

    def _domain_email(self, username: str) -> str:
        return f"{username}{self._config.domain}"


def _first_value(value: Any) -> Any:
    """ldap3 returns attribute values as lists; pull the first element."""
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _extract_attrs(entry: Any, names: list[str]) -> dict[str, Any]:
    """Flatten an ldap3 entry's attributes into ``{name: first_value}``.

    Resilient to missing attributes — AD omits ``mail`` for some accounts.
    """
    out: dict[str, Any] = {}
    try:
        raw = {str(k): list(v) for k, v in entry.entry_attributes_as_dict.items()}  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — fall back to per-attribute access
        raw = {}
        for name in names:
            try:
                vals = entry[name].value  # type: ignore[index]
                raw[name] = vals if isinstance(vals, (list, tuple)) else [vals]
            except Exception:  # noqa: BLE001
                continue
    for name in names:
        out[name] = raw.get(name, [])
    return out


def _escape_ldap(value: str) -> str:
    """Escape a value for use inside an LDAP filter (RFC 4515)."""
    replacements = {
        "\\": r"\5c",
        "*": r"\2a",
        "(": r"\28",
        ")": r"\29",
        "\x00": r"\00",
    }
    out = []
    for ch in value:
        out.append(replacements.get(ch, ch))
    return "".join(out)
