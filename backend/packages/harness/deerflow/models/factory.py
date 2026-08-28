import asyncio
import logging
import time
from typing import Any

from langchain.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig

from deerflow.config import get_app_config
from deerflow.config.app_config import AppConfig
from deerflow.reflection import resolve_class
from deerflow.tracing import build_tracing_callbacks

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider-safe multimodal-message normalisation (applied to every chat model
# instance at the factory boundary).  Fixes Kimi / Moonshot and similar
# providers when user sends:
#   * a pure-image human message (no text part at all) → add "(图片附件)"
#   * a human message whose `content` list contains {"type":"text","text":""}
#     alongside an image → drop the empty text part; otherwise Kimi throws
#     HTTP 400 "text content is empty" even when a valid image_url follows
# ---------------------------------------------------------------------------


def _nmcp_normalize_human_content(message: BaseMessage) -> BaseMessage:
    """Apply provider-safe multimodal normalisation to a SINGLE human message.

    Fixes two classes of Kimi / Moonshot rejection:

    * ``content`` is a JSON list containing an empty ``{"type":"text"}`` block
      alongside image/video/image_url parts → drop the empty text part and
      insert a placeholder text if no non-empty text part remains.
    * ``content`` is a bare **empty string** (``""``) → substitute a short
      placeholder so the provider's "text content is empty" check doesn't
      fire before any other structured part (files, attachments) or
      follow-up turns are even considered.

    Default prompt selection:
    =========================
    When a message has no text part but carries media / file content we
    inject a task-appropriate Chinese prompt so providers (notably Kimi's
    Moonshot API) that require a non-empty text block get a meaningful,
    natural instruction instead of a cryptic placeholder.

    The prompt is chosen by the dominant non-text part:

    * image_url    → "请分析这张图片"
    * file_path    → "请阅读这个文件"
    * video_url    → "请分析这个视频"
    * audio_url    → "请分析这段音频"
    * mixed / other → "请分析附件内容"
    """
    if getattr(message, "type", "") != "human":
        return message
    original = getattr(message, "content", None)

    # --- Empty-string content: treat as missing text. ------------------------
    # The Frontend prompt-input sends content="" when the user only attaches
    # files.  Some backend upload-materialisation paths convert `files` into
    # content *blocks* (image_url, …) later in the pipeline; for those
    # cases the list branch below handles them.  If the message still has a
    # scalar empty string when it reaches the model wrapper, the provider
    # will reject it as "text content is empty" → plug a placeholder.
    if original is None or (isinstance(original, str) and original.strip() == ""):
        placeholder = "请分析附件内容"
        try:
            return message.model_copy(update={"content": placeholder})  # type: ignore[attr-defined]
        except Exception:
            message.content = placeholder  # type: ignore[attr-defined]
            return message

    # Scalar non-empty string: nothing to normalise at this layer.
    if isinstance(original, str):
        return message

    # --- List-content multimodal normalisation. --------------------------------
    if not isinstance(original, list):
        return message

    # 1) Drop empty text parts and gather non-text type hints
    filtered: list = []
    non_text_types: set = set()
    for part in original:
        if not isinstance(part, dict):
            filtered.append(part)
            continue
        part_type = part.get("type")
        if part_type == "text":
            text_val = part.get("text", "") or ""
            if isinstance(text_val, str) and text_val.strip() != "":
                filtered.append(part)
        else:
            non_text_types.add(part_type)
            filtered.append(part)

    # 2) If we have media but no text part → inject a smart default prompt
    if non_text_types and not any(
        isinstance(p, dict) and p.get("type") == "text" for p in filtered
    ):
        dominant = _dominant_media_type(non_text_types)
        default_prompts = {
            "image_url": "请分析这张图片",
            "file_path": "请阅读这个文件",
            "video_url": "请分析这个视频",
            "audio_url": "请分析这段音频",
        }
        prompt = default_prompts.get(dominant, "请分析附件内容")
        filtered.insert(0, {"type": "text", "text": prompt})

    # 3) Safety net: after dropping empty text parts, we might end up with a
    #    zero-length filtered list → fall back to scalar placeholder.
    if not filtered:
        placeholder = "请分析附件内容"
        try:
            return message.model_copy(update={"content": placeholder})  # type: ignore[attr-defined]
        except Exception:
            message.content = placeholder  # type: ignore[attr-defined]
            return message

    if len(filtered) == 1 and isinstance(filtered[0], dict) and filtered[0].get("type") == "text":
        new_content = filtered[0].get("text", "") or ""
    else:
        new_content = filtered
    if new_content == original:
        return message
    try:
        return message.model_copy(update={"content": new_content})  # type: ignore[attr-defined]
    except Exception:
        message.content = new_content  # type: ignore[attr-defined]
        return message


def _dominant_media_type(non_text_types: set) -> str:
    """Pick the most specific media type from a set of non-text part types."""
    # Priority: image > file > video > audio > other
    priority = ["image_url", "image", "file_path", "video_url", "video", "audio_url", "audio"]
    for p in priority:
        if p in non_text_types:
            return p
    return next(iter(non_text_types)) if non_text_types else "other"


def _nmcp_normalize_messages(messages: Any) -> tuple[Any, bool]:
    """Return (normalized_messages, changed) for the three shapes LC accepts."""
    # Single message
    if isinstance(messages, BaseMessage):
        n = _nmcp_normalize_human_content(messages)
        return n, (n is not messages)
    # List[BaseMessage]
    if isinstance(messages, list) and messages:
        normalized = [_nmcp_normalize_human_content(m) for m in messages]
        changed = any(a is not b for a, b in zip(normalized, messages))
        # handle length mismatch — impossible in current impl, but guard it
        if len(normalized) != len(messages):
            changed = True
        return normalized, changed
    # str / list[str] / prompt-value passthrough: skip
    return messages, False


def _is_retryable_llm_error(exc: BaseException) -> bool:
    """Check if an LLM error is transiently retryable (e.g. Kimi 403 concurrency).

    This is intentionally lightweight — runs on every model call, so no
    heavy introspection.  We look for the key patterns that identify
    retriable provider-side failures.
    """
    name = exc.__class__.__name__.lower()
    detail = str(exc).lower()

    # HTTP status 403 + concurrency keyword → Kimi/Moonshot concurrent limit
    if "concurrent" in detail or "concurrent" in name:
        return True
    if "access_terminated" in detail and "limit" in detail:
        return True

    # Transient network / provider errors
    retryable_classes = {
        "apitimeouterror",
        "apiconnectionerror",
        "internalservererror",
        "readerror",  # httpx
        "remoteprotocolerror",  # httpx
        "permissiondeniederror",  # Kimi wraps concurrency 403 as this
    }
    if name in retryable_classes:
        # PermissionDeniedError: only retry if it's a concurrency limit,
        # NOT an auth failure (key really invalid)
        if name == "permissiondeniederror":
            if "concurrent" in detail or "access_terminated" in detail:
                return True
            if "limit" in detail:
                return True
            return False  # real auth failure → don't retry
        return True

    # 429 (rate limit) → always retry
    if "429" in str(exc) or "rate limit" in detail or "too many requests" in detail:
        return True

    return False


def _model_call_retry_wrapper_sync(
    func,
    max_retries: int = 3,
    base_delay_ms: int = 1000,
):
    """Wrap a synchronous model call with retry logic."""
    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        last_exc = None
        for attempt in range(max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                if not _is_retryable_llm_error(exc) or attempt >= max_retries:
                    raise
                wait_ms = base_delay_ms * (2 ** attempt)
                logger.warning(
                    "Transient LLM error (retry %d/%d); retrying in %dms: %s",
                    attempt + 1,
                    max_retries,
                    wait_ms,
                    str(exc)[:200],
                )
                time.sleep(wait_ms / 1000)
        raise last_exc  # type: ignore[misc]

    return wrapper


def _apply_multimodal_normalization_wrapper(instance: BaseChatModel, resolved_name: str) -> None:
    """Wrap ``{a,}_generate`` / ``{a,}_stream`` on a chat model instance so
    every human message is normalised provider-safely right before hitting the
    underlying SDK transport.  Runs AFTER every higher-level middleware /
    flash-direct pre-processing, guaranteeing no path can escape the fix."""
    import functools

    orig_agenerate = instance._agenerate
    orig_generate = instance._generate
    orig_astream = getattr(instance, "_astream", None)
    orig_stream = getattr(instance, "_stream", None)
    orig_ainvoke_raw = getattr(instance, "ainvoke_raw", None)
    orig_invoke_raw = getattr(instance, "invoke_raw", None)

    def _preview(name: str, messages: Any) -> None:
        # Small summary log, bounded and strictly best-effort.
        try:
            parts: list[str] = []
            msgs = messages if isinstance(messages, list) else [messages]
            for i, msg in enumerate(msgs[-8:]):  # keep tail only
                mtype = getattr(msg, "type", "?")
                content = getattr(msg, "content", None)
                if isinstance(content, list):
                    ps = ", ".join(
                        f"{(p.get('type') if isinstance(p, dict) else type(p).__name__)}"
                        + (
                            f"[:20]={str(p.get('text'))[:20]!r}"
                            if isinstance(p, dict) and p.get("type") == "text"
                            else ""
                        )
                        for p in content[:6]
                    )
                    parts.append(f"[{i - len(msgs) + len(msgs[-8:]):+d}] {mtype}({ps})")
                else:
                    parts.append(f"[{i - len(msgs) + len(msgs[-8:]):+d}] {mtype}={str(content)[:40]!r}")
            logger.info(
                "[LLM_INPUT_PREVIEW] model=%s wrapper=%s tail=%s",
                resolved_name,
                name,
                " | ".join(parts),
            )
        except Exception:  # noqa: BLE001
            pass

    async def wrapped_agenerate(messages, *args, **kwargs):
        logger.info("[MODEL_WRAPPER] wrapped_agenerate CALLED (messages=%s)", len(messages) if isinstance(messages, list) else "?")
        nm, changed = _nmcp_normalize_messages(messages)
        _preview("agenerate" + ("(norm)" if changed else ""), nm)
        last_exc = None
        for attempt in range(5):
            try:
                return await orig_agenerate(nm, *args, **kwargs)
            except Exception as exc:
                last_exc = exc
                if not _is_retryable_llm_error(exc) or attempt >= 4:
                    raise
                wait_ms = 1000 * (2 ** attempt)
                logger.warning(
                    "Transient LLM error in agenerate (retry %d/5); retrying in %dms: %s",
                    attempt + 1, wait_ms, str(exc)[:200],
                )
                await asyncio.sleep(wait_ms / 1000)
        raise last_exc  # type: ignore[misc]

    def wrapped_generate(messages, *args, **kwargs):
        nm, changed = _nmcp_normalize_messages(messages)
        _preview("generate" + ("(norm)" if changed else ""), nm)
        return orig_generate(nm, *args, **kwargs)

    object.__setattr__(instance, "_agenerate", wrapped_agenerate)
    object.__setattr__(instance, "_generate", wrapped_generate)

    if callable(orig_astream):

        async def wrapped_astream(messages, *args, **kwargs):
            logger.info("[MODEL_WRAPPER] wrapped_astream CALLED (messages=%s)", len(messages) if isinstance(messages, list) else "?")
            nm, changed = _nmcp_normalize_messages(messages)
            _preview("astream" + ("(norm)" if changed else ""), nm)
            last_exc = None
            for attempt in range(5):
                try:
                    async for chunk in orig_astream(nm, *args, **kwargs):
                        yield chunk
                    return  # success — stop retrying
                except Exception as exc:
                    last_exc = exc
                    if not _is_retryable_llm_error(exc) or attempt >= 4:
                        raise
                    wait_ms = 1000 * (2 ** attempt)
                    logger.warning(
                        "Transient LLM error in astream (retry %d/5); retrying in %dms: %s",
                        attempt + 1, wait_ms, str(exc)[:200],
                    )
                    await asyncio.sleep(wait_ms / 1000)
            raise last_exc  # type: ignore[misc]

        object.__setattr__(instance, "_astream", wrapped_astream)

    if callable(orig_stream):

        def wrapped_stream(messages, *args, **kwargs):
            nm, changed = _nmcp_normalize_messages(messages)
            _preview("stream" + ("(norm)" if changed else ""), nm)
            yield from orig_stream(nm, *args, **kwargs)

        object.__setattr__(instance, "_stream", wrapped_stream)

    if callable(orig_ainvoke_raw):

        async def wrapped_ainvoke_raw(input, *args, **kwargs):  # noqa: A002 — matches signature
            if isinstance(input, (list, BaseMessage)):
                nm, _ = _nmcp_normalize_messages(input)
                _preview("ainvoke_raw", nm)
                return await orig_ainvoke_raw(nm, *args, **kwargs)
            return await orig_ainvoke_raw(input, *args, **kwargs)

        object.__setattr__(instance, "ainvoke_raw", wrapped_ainvoke_raw)

    if callable(orig_invoke_raw):

        def wrapped_invoke_raw(input, *args, **kwargs):  # noqa: A002 — matches signature
            if isinstance(input, (list, BaseMessage)):
                nm, _ = _nmcp_normalize_messages(input)
                _preview("invoke_raw", nm)
                return orig_invoke_raw(nm, *args, **kwargs)
            return orig_invoke_raw(input, *args, **kwargs)

        object.__setattr__(instance, "invoke_raw", wrapped_invoke_raw)

    # ------------------------------------------------------------------
    # Runnable entrypoints — some vendor ChatModels (e.g. ChatOpenAI
    # subclasses) override ``ainvoke`` / ``astream`` / ``invoke`` /
    # ``stream`` directly and bypass the canonical _generate / _stream
    # helpers entirely.  Wrapping the Runnable surface guarantees the
    # normaliser runs even in those cases.  (Normalisation is a pure
    # function so double-application is harmless / no-op.)
    #
    # NOTE: BaseChatModel implementations are frequently Pydantic models
    # (v1 / v2) that refuse `setattr` for names that aren't declared
    # fields.  Internal helpers like ``_agenerate`` (leading underscore)
    # are usually whitelisted by the ``__private_attributes__`` system,
    # but ``ainvoke`` / ``astream`` (Runnable surface names, no
    # underscore) will raise ``ValueError("… has no field ainvoke")`` on
    # popular third-party ChatModel classes.  We use ``object.__setattr__``
    # to bypass Pydantic's field validation — these are genuine methods
    # defined on the class, so replacing them on the instance is safe.
    try:
        orig_ainvoke_runnable = instance.ainvoke
        orig_astream_runnable = instance.astream
        orig_invoke_runnable = instance.invoke
        orig_stream_runnable = instance.stream

        async def _wrapped_runnable_ainvoke(input, config=None, **kwargs):  # noqa: A002
            logger.info("[MODEL_WRAPPER] _wrapped_runnable_ainvoke CALLED")
            if isinstance(input, (list, BaseMessage)):
                nm, _changed = _nmcp_normalize_messages(input)
                _preview("ainvoke-runnable", nm)
                last_exc = None
                for attempt in range(5):
                    try:
                        return await orig_ainvoke_runnable(nm, config=config, **kwargs)
                    except Exception as exc:
                        last_exc = exc
                        if not _is_retryable_llm_error(exc) or attempt >= 4:
                            raise
                        wait_ms = 1000 * (2 ** attempt)
                        logger.warning(
                            "Transient LLM error in ainvoke (retry %d/5); retrying in %dms: %s",
                            attempt + 1, wait_ms, str(exc)[:200],
                        )
                        await asyncio.sleep(wait_ms / 1000)
                raise last_exc  # type: ignore[misc]
            return await orig_ainvoke_runnable(input, config=config, **kwargs)

        def _wrapped_runnable_invoke(input, config=None, **kwargs):  # noqa: A002
            if isinstance(input, (list, BaseMessage)):
                nm, _changed = _nmcp_normalize_messages(input)
                _preview("invoke-runnable", nm)
                return orig_invoke_runnable(nm, config=config, **kwargs)
            return orig_invoke_runnable(input, config=config, **kwargs)

        async def _wrapped_runnable_astream(input, config=None, **kwargs):  # noqa: A002
            logger.info("[MODEL_WRAPPER] _wrapped_runnable_astream CALLED")
            if isinstance(input, (list, BaseMessage)):
                nm, _changed = _nmcp_normalize_messages(input)
                _preview("astream-runnable", nm)
                last_exc = None
                for attempt in range(5):
                    try:
                        async for chunk in orig_astream_runnable(nm, config=config, **kwargs):
                            yield chunk
                        return
                    except Exception as exc:
                        last_exc = exc
                        if not _is_retryable_llm_error(exc) or attempt >= 4:
                            raise
                        wait_ms = 1000 * (2 ** attempt)
                        logger.warning(
                            "Transient LLM error in astream-runnable (retry %d/5); retrying in %dms: %s",
                            attempt + 1, wait_ms, str(exc)[:200],
                        )
                        await asyncio.sleep(wait_ms / 1000)
                raise last_exc  # type: ignore[misc]
            else:
                async for chunk in orig_astream_runnable(input, config=config, **kwargs):
                    yield chunk

        def _wrapped_runnable_stream(input, config=None, **kwargs):  # noqa: A002
            if isinstance(input, (list, BaseMessage)):
                nm, _changed = _nmcp_normalize_messages(input)
                _preview("stream-runnable", nm)
                yield from orig_stream_runnable(nm, config=config, **kwargs)
            else:
                yield from orig_stream_runnable(input, config=config, **kwargs)

        object.__setattr__(instance, "ainvoke", _wrapped_runnable_ainvoke)
        object.__setattr__(instance, "invoke", _wrapped_runnable_invoke)
        object.__setattr__(instance, "astream", _wrapped_runnable_astream)
        object.__setattr__(instance, "stream", _wrapped_runnable_stream)
        runnable_apply_status = "ok"
    except (AttributeError, ValueError, TypeError) as _runnable_patch_err:
        # Pydantic setattr protection or vendor class surface mismatch.
        # Internal helper wrapping applied above still covers BaseChatModel
        # default transport.
        runnable_apply_status = f"skipped ({type(_runnable_patch_err).__name__}: {_runnable_patch_err})"

    # One-time informational log so operator can confirm wrapper is active
    logger.info(
        "Multimodal normalisation wrapper attached to model=%s (agenerate=%s astream=%s runnable=%s) [instance_id=%d type=%s]",
        resolved_name,
        "ok",
        "ok" if callable(orig_astream) else "n/a",
        runnable_apply_status,
        id(instance),
        type(instance).__name__,
    )


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

    # Merge config defaults with explicit kwargs.  Explicit kwargs take precedence
    # so that callers can override configuration values, and the merge avoids
    # TypeError when both dicts contain the same key.
    final_kwargs = {**model_settings_from_config, **kwargs}
    model_instance = model_class(**final_kwargs)

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
    # Attach provider-safe multimodal wrapper before caching — normalisation
    # runs immediately before the httpx transport call, so there is no way
    # for a graph middle, flash-direct prep, or tool node to bypass it.
    _apply_multimodal_normalization_wrapper(instance, resolved_name)
    _chat_model_instance_cache[cache_key] = instance
    return instance
