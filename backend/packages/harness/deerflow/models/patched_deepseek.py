"""Patched ChatDeepSeek that preserves reasoning_content in multi-turn conversations.

This module provides a patched version of ChatDeepSeek that properly handles
reasoning_content when sending messages back to the API. The original implementation
stores reasoning_content in additional_kwargs but doesn't include it when making
subsequent API calls, which causes errors with APIs that require reasoning_content
on all assistant messages when thinking mode is enabled.
"""

import json
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_deepseek import ChatDeepSeek


class PatchedChatDeepSeek(ChatDeepSeek):
    """ChatDeepSeek with proper reasoning_content preservation.

    When using thinking/reasoning enabled models, the API expects reasoning_content
    to be present on ALL assistant messages in multi-turn conversations. This patched
    version ensures reasoning_content from additional_kwargs is included in the
    request payload.
    """

    @classmethod
    def is_lc_serializable(cls) -> bool:
        return True

    @property
    def lc_secrets(self) -> dict[str, str]:
        return {"api_key": "DEEPSEEK_API_KEY", "openai_api_key": "DEEPSEEK_API_KEY"}

    def get_num_tokens_from_messages(
        self,
        messages: Sequence[BaseMessage],
        tools: Sequence[dict[str, Any] | type | Callable | BaseTool] | None = None,
        *,
        allow_fetching_images: bool = True,
    ) -> int:
        """Count compatible model aliases conservatively for quota preflight.

        LangChain only implements its OpenAI message formula for model names
        beginning with ``gpt-*``. DeepSeek-compatible gateways commonly expose
        aliases such as ``kimi-for-coding`` even though they use the same chat
        payload shape. Fall back to tokenizing the canonical request payload and
        add per-message/tool envelope allowances so quota enforcement does not
        underestimate the request.
        """
        try:
            return super().get_num_tokens_from_messages(
                messages,
                tools=tools,
                allow_fetching_images=allow_fetching_images,
            )
        except NotImplementedError:
            payload = self._get_request_payload(list(messages))
            token_payload: dict[str, Any] = {
                "messages": payload.get("messages", []),
            }
            if tools:
                token_payload["tools"] = [convert_to_openai_tool(tool) for tool in tools]
            canonical = json.dumps(
                token_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            envelope_tokens = (4 * len(messages)) + (8 * len(tools or ())) + 3
            return int(self.get_num_tokens(canonical)) + envelope_tokens

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """Get request payload with reasoning_content preserved.

        Overrides the parent method to inject reasoning_content from
        additional_kwargs into assistant messages in the payload.
        """
        # Get the original messages before conversion
        original_messages = self._convert_input(input_).to_messages()

        # Call parent to get the base payload
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        # Match payload messages with original messages to restore reasoning_content
        payload_messages = payload.get("messages", [])

        # The payload messages and original messages should be in the same order
        # Iterate through both and match by position
        if len(payload_messages) == len(original_messages):
            for payload_msg, orig_msg in zip(payload_messages, original_messages):
                if payload_msg.get("role") == "assistant" and isinstance(orig_msg, AIMessage):
                    reasoning_content = orig_msg.additional_kwargs.get("reasoning_content")
                    if reasoning_content is not None:
                        payload_msg["reasoning_content"] = reasoning_content
        else:
            # Fallback: match by counting assistant messages
            ai_messages = [m for m in original_messages if isinstance(m, AIMessage)]
            assistant_payloads = [(i, m) for i, m in enumerate(payload_messages) if m.get("role") == "assistant"]

            for (idx, payload_msg), ai_msg in zip(assistant_payloads, ai_messages):
                reasoning_content = ai_msg.additional_kwargs.get("reasoning_content")
                if reasoning_content is not None:
                    payload_messages[idx]["reasoning_content"] = reasoning_content

        return payload
