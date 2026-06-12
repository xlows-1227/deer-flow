import logging

from langchain.chat_models import BaseChatModel

from deerflow.config import get_app_config
from deerflow.config.app_config import AppConfig
from deerflow.reflection import resolve_class
from deerflow.tracing import build_tracing_callbacks

logger = logging.getLogger(__name__)


def _deep_merge_dicts(base: dict | None, override: dict) -> dict:
    """Recursively merge two dictionaries without mutating the inputs."""
    merged = dict(base or {})
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _vllm_disable_chat_template_kwargs(chat_template_kwargs: dict) -> dict:
    """Build the disable payload for vLLM/Qwen chat template kwargs."""
    disable_kwargs: dict[str, bool] = {}
    if "thinking" in chat_template_kwargs:
        disable_kwargs["thinking"] = False
    if "enable_thinking" in chat_template_kwargs:
        disable_kwargs["enable_thinking"] = False
    return disable_kwargs


def _enable_stream_usage_by_default(model_use_path: str, model_settings_from_config: dict) -> None:
    """Enable stream usage for OpenAI-compatible models unless explicitly configured.

    LangChain only auto-enables ``stream_usage`` for OpenAI models when no custom
    base URL or client is configured. DeerFlow frequently uses OpenAI-compatible
    gateways, so token usage tracking would otherwise stay empty and the
    TokenUsageMiddleware would have nothing to log.
    """
    if model_use_path != "langchain_openai:ChatOpenAI":
        return
    if "stream_usage" in model_settings_from_config:
        return
    if "base_url" in model_settings_from_config or "openai_api_base" in model_settings_from_config:
        model_settings_from_config["stream_usage"] = True


def _normalize_provider_reasoning_effort(model_use_path: str, model_settings_from_config: dict, kwargs: dict) -> None:
    """Normalize frontend reasoning effort labels for providers with narrower enums."""
    if model_use_path not in {
        "langchain_deepseek:ChatDeepSeek",
        "deerflow.models.patched_deepseek:PatchedChatDeepSeek",
    }:
        return

    deepseek_reasoning_effort = {
        "minimal": "low",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "max": "max",
        "xhigh": "xhigh",
    }
    for settings in (model_settings_from_config, kwargs):
        effort = settings.get("reasoning_effort")
        if effort in deepseek_reasoning_effort:
            settings["reasoning_effort"] = deepseek_reasoning_effort[effort]


def create_chat_model(name: str | None = None, thinking_enabled: bool = False, *, app_config: AppConfig | None = None, attach_tracing: bool = True, **kwargs) -> BaseChatModel:
    """Create a chat model instance from the config.

    Args:
        name: The name of the model to create. If None, the first model in the config will be used.
        thinking_enabled: Enable the model's extended-thinking mode when supported.
        app_config: Explicit application config; falls back to the cached global if omitted.
        attach_tracing: When True (default), attach tracing callbacks (Langfuse,
            LangSmith) directly to the model instance. Standalone callers — anything
            that invokes the model outside a LangGraph run that already wires tracing
            at the invocation root (``MemoryUpdater``, ad-hoc utilities, etc.) — keep
            this default so the model-level callback still produces traces. Callers
            that already attach tracing at the graph root (``make_lead_agent``, the
            in-graph ``TitleMiddleware``) MUST pass ``attach_tracing=False``; otherwise
            the same LLM call emits duplicate spans (one rooted at the graph, one at
            the model) and ``session_id`` / ``user_id`` metadata never reach the trace
            because the model becomes a nested observation whose ``langfuse_*`` keys
            get stripped.

    Returns:
        A chat model instance.
    """
    config = app_config or get_app_config()
    if name is None:
        name = config.models[0].name
    model_config = config.get_model_config(name)
    if model_config is None:
        raise ValueError(f"Model {name} not found in config") from None
    model_class = resolve_class(model_config.use, BaseChatModel)

    # Auto-patch ChatDeepSeek to preserve reasoning_content in multi-turn conversations.
    # The DeepSeek API requires reasoning_content to be passed back on all assistant
    # messages when thinking mode is enabled; the stock ChatDeepSeek drops it.
    if model_config.use == "langchain_deepseek:ChatDeepSeek":
        try:
            from langchain_deepseek import ChatDeepSeek
            from deerflow.models.patched_deepseek import PatchedChatDeepSeek

            if model_class is ChatDeepSeek:
                model_class = PatchedChatDeepSeek
        except ImportError:
            pass

    model_settings_from_config = model_config.model_dump(
        exclude_none=True,
        exclude={
            "use",
            "name",
            "display_name",
            "description",
            "supports_thinking",
            "supports_reasoning_effort",
            "when_thinking_enabled",
            "when_thinking_disabled",
            "thinking",
            "supports_vision",
        },
    )
    # Compute effective when_thinking_enabled by merging in the `thinking` shortcut field.
    # The `thinking` shortcut is equivalent to setting when_thinking_enabled["thinking"].
    has_thinking_settings = (model_config.when_thinking_enabled is not None) or (model_config.thinking is not None)
    effective_wte: dict = dict(model_config.when_thinking_enabled) if model_config.when_thinking_enabled else {}
    if model_config.thinking is not None:
        merged_thinking = {**(effective_wte.get("thinking") or {}), **model_config.thinking}
        effective_wte = {**effective_wte, "thinking": merged_thinking}
    if thinking_enabled and has_thinking_settings:
        if not model_config.supports_thinking:
            raise ValueError(f"Model {name} does not support thinking. Set `supports_thinking` to true in the `config.yaml` to enable thinking.") from None
        if effective_wte:
            model_settings_from_config.update(effective_wte)
    if not thinking_enabled:
        if model_config.when_thinking_disabled is not None:
            # User-provided disable settings take full precedence
            model_settings_from_config.update(model_config.when_thinking_disabled)
        elif has_thinking_settings and effective_wte.get("extra_body", {}).get("thinking", {}).get("type"):
            # OpenAI-compatible gateway: thinking is nested under extra_body
            model_settings_from_config["extra_body"] = _deep_merge_dicts(
                model_settings_from_config.get("extra_body"),
                {"thinking": {"type": "disabled"}},
            )
            model_settings_from_config["reasoning_effort"] = "minimal"
            kwargs.pop("reasoning_effort", None)
        elif has_thinking_settings and (disable_chat_template_kwargs := _vllm_disable_chat_template_kwargs(effective_wte.get("extra_body", {}).get("chat_template_kwargs") or {})):
            # vLLM uses chat template kwargs to switch thinking on/off.
            model_settings_from_config["extra_body"] = _deep_merge_dicts(
                model_settings_from_config.get("extra_body"),
                {"chat_template_kwargs": disable_chat_template_kwargs},
            )
        elif has_thinking_settings and effective_wte.get("thinking", {}).get("type"):
            # Native langchain_anthropic: thinking is a direct constructor parameter
            model_settings_from_config["thinking"] = {"type": "disabled"}
    if not model_config.supports_reasoning_effort:
        kwargs.pop("reasoning_effort", None)
        model_settings_from_config.pop("reasoning_effort", None)
    else:
        _normalize_provider_reasoning_effort(model_config.use, model_settings_from_config, kwargs)

    _enable_stream_usage_by_default(model_config.use, model_settings_from_config)

    # For Codex Responses API models: map thinking mode to reasoning_effort
    from deerflow.models.openai_codex_provider import CodexChatModel

    if issubclass(model_class, CodexChatModel):
        # The ChatGPT Codex endpoint currently rejects max_tokens/max_output_tokens.
        model_settings_from_config.pop("max_tokens", None)

        # Use explicit reasoning_effort from frontend if provided (low/medium/high)
        explicit_effort = kwargs.pop("reasoning_effort", None)
        if not thinking_enabled:
            model_settings_from_config["reasoning_effort"] = "none"
        elif explicit_effort and explicit_effort in ("low", "medium", "high", "xhigh"):
            model_settings_from_config["reasoning_effort"] = explicit_effort
        elif "reasoning_effort" not in model_settings_from_config:
            model_settings_from_config["reasoning_effort"] = "medium"

    # For MindIE models: enforce conservative retry defaults.
    # Timeout normalization is handled inside MindIEChatModel itself.
    if getattr(model_class, "__name__", "") == "MindIEChatModel":
        # Enforce max_retries constraint to prevent cascading timeouts.
        model_settings_from_config["max_retries"] = model_settings_from_config.get("max_retries", 1)

    # Ensure stream_usage is enabled so that token usage metadata is available
    # in streaming responses.  LangChain's BaseChatOpenAI only defaults
    # stream_usage=True when no custom base_url/api_base is set, so models
    # hitting third-party endpoints (e.g. doubao, deepseek) silently lose
    # usage data.  We default it to True unless explicitly configured.
    if "stream_usage" not in model_settings_from_config and "stream_usage" not in kwargs:
        if "stream_usage" in getattr(model_class, "model_fields", {}):
            model_settings_from_config["stream_usage"] = True

    model_instance = model_class(**kwargs, **model_settings_from_config)

    if attach_tracing:
        callbacks = build_tracing_callbacks()
        if callbacks:
            existing_callbacks = model_instance.callbacks or []
            model_instance.callbacks = [*existing_callbacks, *callbacks]
            logger.debug(f"Tracing attached to model '{name}' with providers={len(callbacks)}")
    return model_instance


# ---------------------------------------------------------------------------
# Cached model instance factory — avoids the ~2 s penalty of repeatedly
# instantiating ChatOpenAI (which recreates httpx.AsyncClient and re-detects
# system proxies on every call).
# ---------------------------------------------------------------------------

_chat_model_instance_cache: dict[tuple, BaseChatModel] = {}


def get_cached_chat_model(
    name: str | None = None,
    thinking_enabled: bool = False,
    *,
    app_config: AppConfig | None = None,
    **kwargs,
) -> BaseChatModel:
    """Return a cached chat model instance, creating only on cache miss.

    The cache key is derived from the resolved model name, ``thinking_enabled``,
    ``reasoning_effort``, and the model config identity.  This is safe because
    ``BaseChatModel`` instances are stateless w.r.t. inference — the underlying
    HTTP client can be reused across concurrent calls.

    Callers that need distinct ``.with_config()`` or callback overrides should
    apply those *after* retrieving the cached instance so the overrides do not
    pollute the cached object.
    """
    config = app_config or get_app_config()
    resolved_name = name or (config.models[0].name if config.models else None)
    if resolved_name is None:
        raise ValueError("No chat model is configured.")

    model_config = config.get_model_config(resolved_name)
    if model_config is None:
        raise ValueError(f"Model {resolved_name!r} not found in config") from None

    reasoning_effort = kwargs.get("reasoning_effort")
    cache_key = (
        resolved_name,
        thinking_enabled,
        reasoning_effort,
        model_config.use,
        id(model_config),
    )

    cached = _chat_model_instance_cache.get(cache_key)
    if cached is not None:
        logger.debug("Chat model cache hit: %s (thinking=%s, effort=%s)", resolved_name, thinking_enabled, reasoning_effort)
        return cached

    logger.info("Chat model cache miss: %s (thinking=%s, effort=%s)", resolved_name, thinking_enabled, reasoning_effort)
    instance = create_chat_model(
        name=resolved_name,
        thinking_enabled=thinking_enabled,
        app_config=config,
        attach_tracing=False,
        **kwargs,
    )
    _chat_model_instance_cache[cache_key] = instance
    return instance
