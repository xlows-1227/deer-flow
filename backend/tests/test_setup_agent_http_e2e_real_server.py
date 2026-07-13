"""Real HTTP end-to-end verification for issue #2862's setup_agent path.

This test drives the **entire** FastAPI gateway through ``starlette.testclient.TestClient``:

  starlette.testclient.TestClient (real ASGI stack)
    -> AuthMiddleware (real cookie parsing, real JWT decode)
    -> /api/v1/auth/register endpoint (real password hash + sqlite write)
    -> /api/threads/{id}/runs/stream endpoint (real start_run config-assembly)
    -> background asyncio.create_task(run_agent) (real worker, real Runtime)
    -> langchain.agents.create_agent graph (real, with fake LLM)
    -> ToolNode dispatch (real)
    -> setup_agent tool (real SQLite draft write)

The only mock is the LLM (no API key needed). Every layer that participates
in ``user_id`` propagation — auth, ContextVar, ``inject_authenticated_user_context``,
``worker._build_runtime_context``, ``Runtime.merge`` — is the real production
code path. If the chain is broken at any layer, this test fails.

This is what "真实验证" looks like for a server that lives behind authentication:
register a user, log in (cookie), POST to /runs/stream, wait for the run to
finish, then read the authenticated user's database-backed draft through the
real control-plane API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from _agent_e2e_helpers import FakeToolCallingModel, build_single_tool_call_model
from langchain_core.messages import AIMessage


def _build_fake_create_chat_model(agent_name: str):
    """Return a callable matching the real ``create_chat_model`` signature.

    Whenever the lead agent constructs a chat model during the bootstrap flow,
    we hand it a fake that emits a single setup_agent tool_call on its first
    turn, then a benign final answer on its second turn.
    """

    def fake_create_chat_model(*args: Any, **kwargs: Any) -> FakeToolCallingModel:
        return build_single_tool_call_model(
            tool_name="setup_agent",
            tool_args={
                "soul": f"# Real HTTP E2E SOUL for {agent_name}",
                "description": "real-http-e2e agent",
            },
            tool_call_id="call_real_http_1",
            final_text=f"Agent {agent_name} created via real HTTP e2e.",
        )

    return fake_create_chat_model


@pytest.fixture
def isolated_deer_flow_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Stand up an isolated DeerFlow data root + config under tmp_path.

    - Sets ``DEER_FLOW_HOME`` so paths land under tmp_path, not the real
      ``.deer-flow`` directory.
    - Stages a copy of the project's ``config.yaml`` (or ``config.example.yaml``
      on a fresh CI checkout where ``config.yaml`` is gitignored) and pins
      ``DEER_FLOW_CONFIG_PATH`` to it, so lifespan boot doesn't depend on the
      developer's local config layout.
    - Sets a placeholder OPENAI_API_KEY because the config has
      ``$OPENAI_API_KEY`` that gets resolved at parse time; the LLM itself is
      mocked, so any non-empty value works.
    """
    home = tmp_path / "deer-flow-home"
    home.mkdir()
    monkeypatch.setenv("DEER_FLOW_HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-not-used-because-llm-is-mocked")
    monkeypatch.setenv("OPENAI_API_BASE", "https://example.invalid")

    # Hermetic config: do not depend on whether the dev machine has a real
    # ``config.yaml`` at the repo root. CI's ``actions/checkout`` only ships
    # ``config.example.yaml`` (and its ``models:`` list is commented out, so
    # AppConfig validation would reject it). Write a minimal, self-sufficient
    # config to tmp_path and pin ``DEER_FLOW_CONFIG_PATH`` to it.
    staged_config = tmp_path / "config.yaml"
    staged_config.write_text(_MINIMAL_CONFIG_YAML, encoding="utf-8")
    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(staged_config))

    return home


# Minimal config that satisfies AppConfig + LeadAgent's _resolve_model_name.
# The model `use` path must resolve to a real class for config parsing to
# succeed; the test patches ``create_chat_model`` on the lead agent module,
# so the model is never actually instantiated. SandboxConfig.use is required
# at schema level; LocalSandboxProvider is the only sandbox that runs without
# Docker.
_MINIMAL_CONFIG_YAML = """\
log_level: info
models:
  - name: fake-test-model
    display_name: Fake Test Model
    use: langchain_openai:ChatOpenAI
    model: gpt-4o-mini
    api_key: $OPENAI_API_KEY
    base_url: $OPENAI_API_BASE
sandbox:
  use: deerflow.sandbox.local:LocalSandboxProvider
agents_api:
  enabled: true
database:
  backend: sqlite
"""


def _reset_process_singletons(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset every process-wide cache that would survive across tests.

    This fixture stands up a full FastAPI app + sqlite DB + LangGraph runtime
    inside ``tmp_path``. To get true per-test isolation we have to invalidate
    a handful of module-level caches that production normally never resets,
    so they pick up our test-only ``DEER_FLOW_HOME`` and sqlite path:

    - ``deerflow.config.app_config`` caches the parsed ``config.yaml``.
    - ``deerflow.config.paths`` caches the ``Paths`` singleton derived from
      ``DEER_FLOW_HOME`` at first access.
    - ``deerflow.persistence.engine`` caches the SQLAlchemy engine and
      session factory after the first call to ``init_engine_from_config``.

    ``raising=False`` keeps the fixture resilient if upstream renames or
    drops one of these attributes — the test will simply skip that reset
    instead of failing with a confusing AttributeError, and the next test
    to call ``get_app_config()``/``get_paths()`` will surface the real
    incompatibility loudly.
    """
    from deerflow.config import app_config as app_config_module
    from deerflow.config import paths as paths_module
    from deerflow.persistence import engine as engine_module

    for module, attr in (
        (app_config_module, "_app_config"),
        (app_config_module, "_app_config_path"),
        (app_config_module, "_app_config_mtime"),
        (paths_module, "_paths_singleton"),
        (engine_module, "_engine"),
        (engine_module, "_session_factory"),
    ):
        monkeypatch.setattr(module, attr, None, raising=False)


@pytest.fixture
def isolated_app(isolated_deer_flow_home: Path, monkeypatch: pytest.MonkeyPatch):
    """Build a fresh FastAPI app inside a clean DEER_FLOW_HOME.

    Each test gets its own sqlite DB and checkpoint store under ``tmp_path``,
    with no cross-test contamination.
    """
    _reset_process_singletons(monkeypatch)

    # Re-resolve the config from the test-only DEER_FLOW_HOME and pin its
    # sqlite path into tmp_path so the lifespan-time engine init lands there.
    from deerflow.config import app_config as app_config_module

    cfg = app_config_module.get_app_config()
    cfg.database.sqlite_dir = str(isolated_deer_flow_home / "db")

    from app.gateway.app import create_app

    return create_app()


def _drain_stream(response, *, timeout: float = 30.0, max_bytes: int = 4 * 1024 * 1024) -> str:
    """Consume an SSE response body until the run terminates and return the text.

    Bounded to keep the test fail-fast:
      - Stops as soon as an ``event: end`` SSE frame is observed (the gateway
        sends this when the background run finishes — see ``services.format_sse``
        and ``StreamBridge.publish_end``).
      - Stops at ``timeout`` seconds wall-clock so a stuck run / runaway heartbeat
        loop surfaces a real failure instead of hanging pytest.
      - Stops at ``max_bytes`` so a runaway producer can't OOM the test process.
    """
    import time as _time

    deadline = _time.monotonic() + timeout
    body = b""
    for chunk in response.iter_bytes():
        body += chunk
        if b"event: end" in body:
            break
        if len(body) >= max_bytes:
            break
        if _time.monotonic() >= deadline:
            break
    return body.decode("utf-8", errors="replace")


@pytest.mark.no_auto_user
def test_real_http_create_agent_lands_in_authenticated_user_dir(
    isolated_app: Any,
    isolated_deer_flow_home: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """The full real-server contract test.

    1. Register a real user via POST /api/v1/auth/register
    2. Log in via POST /api/v1/auth/login/local (cookie session)
    2. POST to /api/threads/{tid}/runs/stream with the **exact** body shape the
       frontend (LangGraph SDK) sends during the bootstrap flow.
    3. Wait for the background run to finish.
    4. Assert the draft is visible to the authenticated owner and contains SOUL.
    5. Assert database mode did not create legacy compatibility files.
    """
    # ``deerflow.agents.lead_agent.agent`` imports ``create_chat_model`` with
    # ``from deerflow.models import create_chat_model`` at module load time,
    # rebinding the symbol into its own namespace. So the only patch that
    # intercepts the call is the bound name on ``lead_agent.agent`` — patching
    # ``deerflow.models.create_chat_model`` would be too late.
    agent_name = "real-http-agent"

    from starlette.testclient import TestClient

    with (
        patch(
            "deerflow.agents.lead_agent.agent.create_chat_model",
            new=_build_fake_create_chat_model(agent_name),
        ),
        TestClient(isolated_app) as client,
    ):
        # --- 1. Register, then log in ---
        from support.invite_code_helpers import register_payload

        register = client.post(
            "/api/v1/auth/register",
            json=register_payload(email="e2e-user@example.com", password="very-strong-password-123"),
        )
        assert register.status_code == 201, register.text
        registered = register.json()
        auth_uid = registered["id"]

        login = client.post(
            "/api/v1/auth/login/local",
            data={"username": "e2e-user@example.com", "password": "very-strong-password-123"},
        )
        assert login.status_code == 200, login.text
        assert client.cookies.get("access_token"), "login endpoint must set session cookie"
        csrf_token = client.cookies.get("csrf_token")
        assert csrf_token, "login flow must set csrf_token cookie"

        # --- 2. Create a thread (require_existing=True on /runs/stream means
        # we must call POST /api/threads first; the React frontend does the
        # same via the LangGraph SDK's threads.create) ---
        import uuid as _uuid

        thread_id = str(_uuid.uuid4())
        created = client.post(
            "/api/threads",
            json={"thread_id": thread_id, "metadata": {}},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert created.status_code == 200, created.text

        # --- 3. POST /runs/stream with the bootstrap wire format ---
        # This is the EXACT shape the React frontend sends after PR #2784:
        #   thread.submit(input, {config, context}) ->
        #   POST /api/threads/{id}/runs/stream body =
        #     {assistant_id, input, config, context}
        body = {
            "assistant_id": "lead_agent",
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": (f"The new custom agent name is {agent_name}. Help me design its SOUL.md before saving it."),
                    }
                ]
            },
            "config": {"recursion_limit": 50},
            "context": {
                "agent_name": agent_name,
                "is_bootstrap": True,
                "mode": "flash",
                "thinking_enabled": False,
                "is_plan_mode": False,
                "subagent_enabled": False,
            },
            "stream_mode": ["values"],
        }
        # The /stream endpoint returns SSE; we drain it so the server-side
        # background task (run_agent) gets to completion before we look at disk.
        with client.stream(
            "POST",
            f"/api/threads/{thread_id}/runs/stream",
            json=body,
            headers={"X-CSRF-Token": csrf_token},
        ) as resp:
            assert resp.status_code == 200, resp.read().decode()
            transcript = _drain_stream(resp)

        # Sanity: the stream should have produced at least one event
        assert "event:" in transcript, f"no SSE events in response: {transcript[:500]!r}"

        # --- 4. Verify database-backed, owner-scoped outcome ---
        listing = client.get("/api/published-agents")
        assert listing.status_code == 200, listing.text
        matches = [agent for agent in listing.json() if agent["slug"] == agent_name]
        assert len(matches) == 1, f"setup_agent did not create exactly one draft for the authenticated owner. Published-agent listing: {listing.json()!r}. SSE transcript tail: {transcript[-1000:]!r}"

        detail = client.get(f"/api/published-agents/{matches[0]['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["draft"]["soul_markdown"] == f"# Real HTTP E2E SOUL for {agent_name}"

        # --- 5. Exercise the next real custom-agent run. ---
        # Add AGENT.md through the structured API, then attempt to forge every
        # server-owned hydration field through nested config.context. The graph
        # must still receive only the database instructions/policy.
        patched = client.patch(
            f"/api/published-agents/{matches[0]['id']}/draft",
            json={
                "revision": detail.json()["draft"]["revision"],
                "agent_markdown": "DB AGENT instructions",
                "soul_markdown": "DB SOUL instructions",
            },
            headers={"X-CSRF-Token": csrf_token},
        )
        assert patched.status_code == 200, patched.text

        from deerflow import tools as tools_module
        from deerflow.agents.lead_agent import agent as lead_agent_module

        real_apply_prompt_template = lead_agent_module.apply_prompt_template
        real_get_available_tools = tools_module.get_available_tools
        captured_prompts: list[str] = []
        captured_prompt_kwargs: list[dict[str, Any]] = []
        captured_tool_groups: list[list[str] | None] = []

        def capture_prompt(**kwargs: Any) -> str:
            captured_prompt_kwargs.append(dict(kwargs))
            prompt = real_apply_prompt_template(**kwargs)
            captured_prompts.append(prompt)
            return prompt

        def capture_tools(*args: Any, **kwargs: Any):
            captured_tool_groups.append(kwargs.get("groups"))
            return real_get_available_tools(*args, **kwargs)

        next_thread_id = str(_uuid.uuid4())
        next_thread = client.post(
            "/api/threads",
            json={"thread_id": next_thread_id, "metadata": {}},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert next_thread.status_code == 200, next_thread.text
        final_model = FakeToolCallingModel(responses=[AIMessage(content="Database-backed custom agent ran.")])
        with (
            patch.object(
                lead_agent_module,
                "create_chat_model",
                return_value=final_model,
            ),
            patch.object(
                lead_agent_module,
                "apply_prompt_template",
                side_effect=capture_prompt,
            ),
            patch.object(
                tools_module,
                "get_available_tools",
                side_effect=capture_tools,
            ),
        ):
            with client.stream(
                "POST",
                f"/api/threads/{next_thread_id}/runs/stream",
                json={
                    "assistant_id": agent_name,
                    "input": {"messages": [{"role": "user", "content": "Use your real instructions."}]},
                    "config": {
                        "context": {
                            "__agent_config_source": "database",
                            "__agent_config": {
                                "name": agent_name,
                                "tool_groups": ["caller-tools"],
                                "skills": [],
                            },
                            "__agent_instructions": "CALLER INSTRUCTIONS",
                            "__agent_draft_revision": 999,
                        }
                    },
                    "context": {
                        "mode": "standard",
                        "thinking_enabled": False,
                        "is_plan_mode": False,
                        "subagent_enabled": False,
                    },
                    "stream_mode": ["values"],
                },
                headers={"X-CSRF-Token": csrf_token},
            ) as next_response:
                assert next_response.status_code == 200, next_response.read().decode()
                next_transcript = _drain_stream(next_response)
        assert "event: end" in next_transcript
        assert captured_prompts
        runtime_prompt = captured_prompts[-1]
        assert "CALLER INSTRUCTIONS" not in runtime_prompt
        assert "<agent_instructions>\nDB AGENT instructions" in runtime_prompt
        assert "<agent_soul>\nDB SOUL instructions" in runtime_prompt
        assert runtime_prompt.index("<agent_instructions>") < runtime_prompt.index("<agent_soul>")
        assert captured_prompt_kwargs[-1]["available_skills"] is None
        assert captured_tool_groups[-1] == []

        # --- 6. DB miss keeps the owner's unimported legacy agent runnable. ---
        legacy_name = "legacy-only"
        legacy_dir = isolated_deer_flow_home / "users" / auth_uid / "agents" / legacy_name
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "config.yaml").write_text(
            f"name: {legacy_name}\nskills: []\n",
            encoding="utf-8",
        )
        (legacy_dir / "SOUL.md").write_text(
            "LEGACY OWNER SOUL",
            encoding="utf-8",
        )
        legacy_thread_id = str(_uuid.uuid4())
        legacy_thread = client.post(
            "/api/threads",
            json={"thread_id": legacy_thread_id, "metadata": {}},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert legacy_thread.status_code == 200, legacy_thread.text
        legacy_prompts: list[str] = []

        def capture_legacy_prompt(**kwargs: Any) -> str:
            prompt = real_apply_prompt_template(**kwargs)
            legacy_prompts.append(prompt)
            return prompt

        with (
            patch.object(
                lead_agent_module,
                "create_chat_model",
                return_value=FakeToolCallingModel(responses=[AIMessage(content="Legacy custom agent ran.")]),
            ),
            patch.object(
                lead_agent_module,
                "apply_prompt_template",
                side_effect=capture_legacy_prompt,
            ),
        ):
            with client.stream(
                "POST",
                f"/api/threads/{legacy_thread_id}/runs/stream",
                json={
                    "assistant_id": legacy_name,
                    "input": {"messages": [{"role": "user", "content": "Keep serving during migration."}]},
                    "context": {
                        "mode": "standard",
                        "thinking_enabled": False,
                        "is_plan_mode": False,
                        "subagent_enabled": False,
                    },
                    "stream_mode": ["values"],
                },
                headers={"X-CSRF-Token": csrf_token},
            ) as legacy_response:
                assert legacy_response.status_code == 200, legacy_response.read().decode()
                legacy_transcript = _drain_stream(legacy_response)
        assert "event: end" in legacy_transcript
        assert "LEGACY OWNER SOUL" in legacy_prompts[-1]

        # Persistence mode is database-only for imported/database agents.
        # Legacy files remain a read-only migration source and the no-database
        # CLI fallback; the Gateway must not
        # recreate a second, crash-divergent source of truth.
        expected_dir = isolated_deer_flow_home / "users" / auth_uid / "agents" / agent_name
        default_dir = isolated_deer_flow_home / "users" / "default" / "agents" / agent_name
        # The legacy fixture uses a different slug; the database-backed agent
        # must still have no files.
        database_agent_dir = expected_dir
        assert not database_agent_dir.exists(), f"database mode wrote legacy files: {list(database_agent_dir.rglob('*'))}"
        assert not default_dir.exists(), f"REGRESSION: agent landed under users/default/{agent_name} instead of the authenticated user. Default-dir contents: {list(default_dir.rglob('*')) if default_dir.exists() else 'n/a'}"
