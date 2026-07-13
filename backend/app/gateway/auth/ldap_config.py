"""LDAP authentication configuration for DeerFlow.

Mirrors the YumChina Spring Boot ``yum.ldap`` block. All settings come from
environment variables so deployment can be reconfigured without code
changes. When ``AUTH_LDAP_ENABLED`` is unset / ``false``, LDAP is fully
disabled and every login falls back to the local provider.

Environment variables
---------------------
- ``AUTH_LDAP_ENABLED``: ``true``/``false`` master switch.
- ``AUTH_LDAP_URL``: LDAP server URL, e.g. ``ldap://ldap.yumchina.com``.
- ``AUTH_LDAP_BASE``: search base DN, e.g. ``ou=YumChina,DC=cn,DC=YumChina,DC=com``.
  Spaces around ``=`` are tolerated and stripped (the supplied Spring
  config had ``DC =com`` style spacing).
- ``AUTH_LDAP_BIND_USERNAME`` / ``AUTH_LDAP_BIND_PASSWORD``: service-account
  credentials used for the search bind.
- ``AUTH_LDAP_OBJECTCLASS``: the attribute whose value is the unique login
  handle (defaults to ``sAMAccountName`` to match YumChina's mixed
  employee / external setup).
- ``AUTH_LDAP_ATTR_REALNAME`` / ``AUTH_LDAP_ATTR_SN`` / ``AUTH_LDAP_ATTR_EMAIL``:
  LDAP attribute names for display name / surname / mail.
- ``AUTH_LDAP_LOCAL_ADMIN_EMAIL``: the single account that always logs in
  against the local password store even when LDAP is enabled.
- ``AUTH_LDAP_DOMAIN``: optional mail domain appended when an LDAP entry
  has no ``mail`` attribute (e.g. ``@yumchina.com``).
"""

from __future__ import annotations

import logging
import os
import re

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _truthy(raw: str | None) -> bool:
    return bool(raw) and raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_base_dn(raw: str) -> str:
    """Strip stray whitespace inside a DN like ``DC =com`` → ``DC=com``.

    The YumChina Spring config shipped with spaces around some ``=`` signs.
    ``ldap3`` does not tolerate that, so we normalise by removing any
    whitespace that sits immediately before or after the ``=`` separators
    while preserving the case and the component order.
    """
    if not raw:
        return ""
    # Collapse whitespace around '=' that separates RDN attribute from value,
    # but only inside DN components. Handled per-component to avoid touching
    # values that legitimately contain commas.
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    cleaned = []
    for part in parts:
        # "DC =com" -> "DC=com"; "ou=YumChina" stays as-is.
        part = re.sub(r"\s*=\s*", "=", part, count=1)
        cleaned.append(part)
    return ",".join(cleaned)


class LdapConfig(BaseModel):
    """Resolved LDAP configuration. ``enabled=False`` disables LDAP entirely."""

    enabled: bool = Field(default=False)
    url: str = Field(default="")
    base: str = Field(default="")
    bind_username: str = Field(default="")
    bind_password: str = Field(default="")
    objectclass: str = Field(default="sAMAccountName")
    attr_realname: str = Field(default="givenName")
    attr_sn: str = Field(default="sn")
    attr_email: str = Field(default="mail")
    local_admin_email: str = Field(default="admin@yumchina.com")
    domain: str = Field(default="@yumchina.com")

    def is_admin_email(self, identifier: str) -> bool:
        """Return True if ``identifier`` matches the configured local admin.

        Accepts either the full admin email (``admin@yumchina.com``) or the
        bare local part (``admin``), case-insensitive. Used by the login
        router to decide whether a credential should bypass LDAP.
        """
        if not identifier:
            return False
        target = identifier.strip().lower()
        admin = self.local_admin_email.strip().lower()
        if not admin:
            return False
        if target == admin:
            return True
        # Bare local-part match (e.g. "admin" vs "admin@yumchina.com").
        if "@" in admin and target == admin.split("@", 1)[0]:
            return True
        return False


def load_ldap_config_from_env() -> LdapConfig:
    """Build a :class:`LdapConfig` from current process environment.

    Kept as a free function (rather than a classmethod) so tests can point
    ``os.environ`` at will and re-resolve. ``AuthConfig`` calls this once at
    first access.
    """
    enabled = _truthy(os.getenv("AUTH_LDAP_ENABLED"))
    url = os.getenv("AUTH_LDAP_URL", "").strip()
    base = _normalize_base_dn(os.getenv("AUTH_LDAP_BASE", ""))
    bind_username = os.getenv("AUTH_LDAP_BIND_USERNAME", "").strip()
    bind_password = os.getenv("AUTH_LDAP_BIND_PASSWORD", "")
    objectclass = os.getenv("AUTH_LDAP_OBJECTCLASS", "sAMAccountName").strip() or "sAMAccountName"
    attr_realname = os.getenv("AUTH_LDAP_ATTR_REALNAME", "givenName").strip() or "givenName"
    attr_sn = os.getenv("AUTH_LDAP_ATTR_SN", "sn").strip() or "sn"
    attr_email = os.getenv("AUTH_LDAP_ATTR_EMAIL", "mail").strip() or "mail"
    local_admin_email = os.getenv("AUTH_LDAP_LOCAL_ADMIN_EMAIL", "admin@yumchina.com").strip() or "admin@yumchina.com"
    domain = os.getenv("AUTH_LDAP_DOMAIN", "@yumchina.com").strip() or "@yumchina.com"

    # Downgrade to disabled if the URL/base are missing — LDAP cannot work
    # without them. We log loudly so operators notice the misconfiguration.
    if enabled and (not url or not base):
        logger.warning("AUTH_LDAP_ENABLED=true but AUTH_LDAP_URL or AUTH_LDAP_BASE is empty; LDAP login disabled. Set both or turn off AUTH_LDAP_ENABLED.")
        enabled = False

    return LdapConfig(
        enabled=enabled,
        url=url,
        base=base,
        bind_username=bind_username,
        bind_password=bind_password,
        objectclass=objectclass,
        attr_realname=attr_realname,
        attr_sn=attr_sn,
        attr_email=attr_email,
        local_admin_email=local_admin_email,
        domain=domain if domain.startswith("@") else f"@{domain}",
    )
