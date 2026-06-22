"""CSRF protection middleware for FastAPI.

Per RFC-001:
State-changing operations require CSRF protection.
"""

import ipaddress
import logging
import os
import secrets
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_TOKEN_LENGTH = 64  # bytes


def _load_trusted_proxy_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parse GATEWAY_TRUSTED_PROXIES into a list of networks.

    Examples:
        GATEWAY_TRUSTED_PROXIES=127.0.0.1,10.0.0.0/8,::1

    If unset, only loopback addresses are trusted. In containerized deployments
    behind a reverse proxy, explicitly set ``GATEWAY_TRUSTED_PROXIES`` to the
    exact proxy CIDRs (e.g. the Docker network containing nginx).
    """
    raw = os.environ.get("GATEWAY_TRUSTED_PROXIES", "127.0.0.1,::1")
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            logger.warning(
                "Invalid GATEWAY_TRUSTED_PROXIES entry %r; skipping. Expected an IP address or CIDR.",
                item,
            )
            continue
    return networks


_TRUSTED_PROXY_NETWORKS = _load_trusted_proxy_networks()


def _is_trusted_proxy(client_host: str | None) -> bool:
    """Return True if ``client_host`` is a trusted proxy."""
    if not client_host:
        return False
    try:
        addr = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    return any(addr in network for network in _TRUSTED_PROXY_NETWORKS)


def is_secure_request(request: Request) -> bool:
    """Detect whether the original client request was made over HTTPS."""
    return _request_scheme(request) == "https"


def generate_csrf_token() -> str:
    """Generate a secure random CSRF token."""
    return secrets.token_urlsafe(CSRF_TOKEN_LENGTH)


def should_check_csrf(request: Request) -> bool:
    """Determine if a request needs CSRF validation.

    CSRF is checked for state-changing methods (POST, PUT, DELETE, PATCH).
    GET, HEAD, OPTIONS, and TRACE do not require a token per RFC 7231. When
    clients voluntarily send a CSRF header on those methods, the middleware
    still validates it in ``dispatch`` instead of silently accepting garbage.
    """
    if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
        return False

    path = request.url.path.rstrip("/")
    # Exempt /api/v1/auth/me endpoint
    if path == "/api/v1/auth/me":
        return False
    return True


_AUTH_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/auth/login/local",
        "/api/v1/auth/logout",
        "/api/v1/auth/register",
        "/api/v1/auth/initialize",
    }
)


def is_auth_endpoint(request: Request) -> bool:
    """Check if the request is to an auth endpoint.

    Auth endpoints don't need CSRF validation on first call (no token).
    """
    return request.url.path.rstrip("/") in _AUTH_EXEMPT_PATHS


def _host_with_optional_port(hostname: str, port: int | None, scheme: str) -> str:
    """Return normalized host[:port], omitting default ports."""
    host = hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    if port is None or (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        return host
    return f"{host}:{port}"


def _normalize_origin(origin: str) -> str | None:
    """Return a normalized scheme://host[:port] origin, or None for invalid input."""
    try:
        parsed = urlsplit(origin.strip())
        port = parsed.port
    except ValueError:
        return None

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        return None

    # Browser Origin is only scheme/host/port. Reject URL-shaped or credentialed values.
    if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        return None

    return f"{scheme}://{_host_with_optional_port(parsed.hostname, port, scheme)}"


def _configured_cors_origins() -> set[str]:
    """Return explicit configured browser origins that may call auth routes."""
    origins = set()
    for raw_origin in os.environ.get("GATEWAY_CORS_ORIGINS", "").split(","):
        origin = raw_origin.strip()
        if not origin or origin == "*":
            continue
        normalized = _normalize_origin(origin)
        if normalized:
            origins.add(normalized)
    return origins


def get_configured_cors_origins() -> set[str]:
    """Return normalized explicit browser origins from GATEWAY_CORS_ORIGINS."""
    return _configured_cors_origins()


def _first_header_value(value: str | None) -> str | None:
    """Return the first value from a comma-separated proxy header."""
    if not value:
        return None
    first = value.split(",", 1)[0].strip()
    return first or None


def _forwarded_param(request: Request, name: str) -> str | None:
    """Extract a parameter from the first RFC 7239 Forwarded header entry."""
    forwarded = _first_header_value(request.headers.get("forwarded"))
    if not forwarded:
        return None

    for part in forwarded.split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key.lower() == name:
            return value.strip().strip('"') or None
    return None


def _request_scheme(request: Request) -> str:
    """Resolve the original request scheme from trusted proxy headers."""
    if _is_trusted_proxy(request.client.host if request.client else None):
        scheme = _forwarded_param(request, "proto") or _first_header_value(request.headers.get("x-forwarded-proto"))
        if scheme:
            return scheme.lower()
    return request.url.scheme.lower()


def _request_origin(request: Request) -> str | None:
    """Build the origin for the URL the browser is targeting.

    Forwarded headers are only honored when the immediate peer is a trusted
    proxy.  In direct-gateway / dev mode a client can otherwise forge both
    Origin and forwarded headers to bypass the login-CSRF guard.
    """
    if _is_trusted_proxy(request.client.host if request.client else None):
        scheme = _forwarded_param(request, "proto") or _first_header_value(request.headers.get("x-forwarded-proto")) or request.url.scheme
        host = _forwarded_param(request, "host") or _first_header_value(request.headers.get("x-forwarded-host")) or request.headers.get("host") or request.url.netloc

        forwarded_port = _first_header_value(request.headers.get("x-forwarded-port"))
        if forwarded_port and ":" not in host.rsplit("]", 1)[-1]:
            host = f"{host}:{forwarded_port}"
    else:
        scheme = request.url.scheme
        host = request.headers.get("host") or request.url.netloc

    return _normalize_origin(f"{scheme}://{host}")


def is_allowed_auth_origin(request: Request) -> bool:
    """Allow auth POSTs only from the same origin or explicit configured origins.

    Login/register/initialize are exempt from the double-submit token because
    first-time browser clients do not have a CSRF token yet. They still create
    a session cookie, so browser requests with a hostile Origin header must be
    rejected to prevent login CSRF / session fixation. Requests without Origin
    are allowed for non-browser clients such as curl and mobile integrations.
    """
    origin = request.headers.get("origin")
    if not origin:
        return True

    normalized_origin = _normalize_origin(origin)
    if normalized_origin is None:
        return False

    request_origin = _request_origin(request)
    return normalized_origin in _configured_cors_origins() or (request_origin is not None and normalized_origin == request_origin)


class CSRFMiddleware(BaseHTTPMiddleware):
    """Middleware that implements CSRF protection using Double Submit Cookie pattern."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        _is_auth = is_auth_endpoint(request)
        api_key_authenticated = getattr(request.state, "auth_method", None) == "api_key"
        csrf_required = should_check_csrf(request)
        header_token = request.headers.get(CSRF_HEADER_NAME)
        csrf_supplied = header_token is not None

        if csrf_required and _is_auth and not is_allowed_auth_origin(request):
            return JSONResponse(
                status_code=403,
                content={"detail": "Cross-site auth request denied."},
            )

        # Unsafe methods require a double-submit token. Safe methods do not
        # require one, but a supplied X-CSRF-Token must still be valid. This
        # prevents any API endpoint from silently accepting forged CSRF
        # credentials while preserving normal header-free GET requests.
        if (csrf_required or csrf_supplied) and not _is_auth and not api_key_authenticated:
            cookie_token = request.cookies.get(CSRF_COOKIE_NAME)

            if not cookie_token or not header_token:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF token missing. Include X-CSRF-Token header."},
                )

            if not secrets.compare_digest(cookie_token, header_token):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF token mismatch."},
                )

        response = await call_next(request)

        # For auth endpoints that set up session, also set CSRF cookie
        if _is_auth and request.method == "POST":
            # Generate a new CSRF token for the session
            csrf_token = generate_csrf_token()
            is_https = is_secure_request(request)
            response.set_cookie(
                key=CSRF_COOKIE_NAME,
                value=csrf_token,
                httponly=False,  # Must be JS-readable for Double Submit Cookie pattern
                secure=is_https,
                samesite="strict",
            )

        return response


def get_csrf_token(request: Request) -> str | None:
    """Get the CSRF token from the current request's cookies.

    This is useful for server-side rendering where you need to embed
    token in forms or headers.
    """
    return request.cookies.get(CSRF_COOKIE_NAME)
