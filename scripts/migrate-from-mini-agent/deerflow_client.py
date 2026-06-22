"""HTTP client for the deer-flow threads API.

Talks to the gateway endpoints defined in
``backend/app/gateway/routers/threads.py``. The migration only needs three
operations:

* ``POST /api/threads``                 — create an empty thread
* ``POST /api/threads/{id}/state``      — inject the message list + title

We avoid the LangGraph SDK here to keep the script dependency-free (stdlib +
``requests`` only).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class DeerFlowError(RuntimeError):
    """Raised when the deer-flow API rejects a call."""


class DeerFlowClient:
    """Thin synchronous wrapper around the deer-flow threads REST API."""

    def __init__(self, base_url: str, timeout: float = 60.0, auth_token: str | None = None):
        # Normalise: strip trailing slash. ``base_url`` should point at the
        # origin that serves ``/api/...`` (e.g. ``http://localhost:3000``).
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.auth_token = auth_token

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
    ) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise DeerFlowError(
                f"{method} {path} -> HTTP {exc.code}: {detail[:500]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise DeerFlowError(f"{method} {path} -> network error: {exc.reason}") from exc

        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise DeerFlowError(f"{method} {path} -> bad JSON: {exc}") from exc

    def create_thread(self, metadata: dict | None = None) -> str:
        """Create an empty thread, return its thread_id."""
        payload: dict[str, Any] = {"metadata": metadata or {}}
        resp = self._request("POST", "/api/threads", payload)
        thread_id = resp.get("thread_id")
        if not thread_id:
            raise DeerFlowError(f"create_thread returned no thread_id: {resp}")
        return thread_id

    def update_state(self, thread_id: str, values: dict) -> dict:
        """Merge ``values`` into the thread's latest checkpoint channel values.

        This is how we inject the recovered messages and the title in a single
        call. See ``update_thread_state`` in threads.py: it does a plain
        ``channel_values.update(body.values)`` before writing the checkpoint,
        so passing ``{"messages": [...], "title": "..."}`` lands both.
        """
        path = f"/api/threads/{thread_id}/state"
        return self._request("POST", path, {"values": values})

    def inject_messages(self, thread_id: str, messages: list[dict], title: str) -> None:
        """Write the recovered conversation into the thread.

        ``messages`` must already be in LangGraph message-dict format (see
        :mod:`converter`). The title is written through the same ``values``
        dict so ``update_thread_state`` syncs it into ``threads_meta``.
        """
        self.update_state(thread_id, {"messages": messages, "title": title})
