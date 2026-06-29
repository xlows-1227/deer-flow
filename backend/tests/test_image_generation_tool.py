import base64
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.gateway.routers.tools import (
    ImageGenerationConfigUpdateRequest,
    ImageGenerationProviderConfigUpdate,
    _extensions_config_path_for_write,
    _provider_update_to_raw,
    update_image_generation_configuration,
)
from deerflow.config.extensions_config import ExtensionsConfig, reset_extensions_config
from deerflow.tools.builtins.generate_image_tool import generate_image_tool
from deerflow.tools.image_generation import (
    GeneratedImage,
    ImageGenerationConfigError,
    ImageGenerationProviderError,
    generate_images,
    get_effective_image_generation_config,
)
from deerflow.tools.tools import get_available_tools

generate_image_module = importlib.import_module("deerflow.tools.builtins.generate_image_tool")
image_generation_module = importlib.import_module("deerflow.tools.image_generation")

PNG_BYTES = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _make_thread_data(tmp_path: Path) -> dict[str, str]:
    user_data = tmp_path / "threads" / "thread-1" / "user-data"
    workspace = user_data / "workspace"
    uploads = user_data / "uploads"
    outputs = user_data / "outputs"
    for directory in (workspace, uploads, outputs):
        directory.mkdir(parents=True)

    return {
        "workspace_path": str(workspace),
        "uploads_path": str(uploads),
        "outputs_path": str(outputs),
    }


def _make_runtime(thread_data: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(
        state={"thread_data": thread_data},
        context={"thread_id": "thread-1"},
        config={},
    )


def _message_content(result) -> str:
    return result.update["messages"][0].content


def _make_minimal_app_config(extensions: ExtensionsConfig | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        tools=[],
        models=[],
        tool_search=SimpleNamespace(enabled=False),
        skill_evolution=SimpleNamespace(enabled=False),
        sandbox=SimpleNamespace(),
        acp_agents={},
        connectors=SimpleNamespace(enabled=False),
        extensions=extensions or ExtensionsConfig(),
    )


def test_effective_image_generation_config_merges_provider_defaults() -> None:
    config = ExtensionsConfig.model_validate(
        {
            "imageGeneration": {
                "enabled": True,
                "providers": {
                    "openai": {
                        "enabled": True,
                        "api_key": "sk-test",
                    }
                },
            }
        }
    )

    effective = get_effective_image_generation_config(config)

    assert effective.enabled is True
    assert effective.default_provider == "openai"
    assert effective.providers["openai"].enabled is True
    assert effective.providers["openai"].api_key == "sk-test"
    assert effective.providers["openai"].model == "gpt-image-1"
    assert effective.providers["openai"].base_url == "https://api.openai.com/v1"
    assert "stability" in effective.providers
    assert effective.providers["aihubmix"].model == "openai/gpt-image-2-free"
    assert effective.providers["aihubmix"].base_url == "https://aihubmix.com/v1"
    assert effective.providers["minimax"].model == "image-01"
    assert effective.providers["minimax"].base_url == "https://api.minimaxi.com/v1"


def test_generate_images_rejects_unsupported_provider_parameter_before_http() -> None:
    config = ExtensionsConfig.model_validate(
        {
            "imageGeneration": {
                "enabled": True,
                "defaultProvider": "stability",
                "providers": {
                    "stability": {
                        "enabled": True,
                        "api_key": "stability-key",
                    }
                },
            }
        }
    )

    with pytest.raises(ImageGenerationConfigError) as exc_info:
        generate_images(prompt="a small cabin", quality="hd", config=config)

    assert "does not support parameter(s): quality" in str(exc_info.value)


def test_available_tools_reads_latest_image_generation_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "extensions_config.json"
    config_path.write_text(
        json.dumps(
            {
                "imageGeneration": {
                    "enabled": True,
                    "defaultProvider": "aihubmix",
                    "providers": {
                        "aihubmix": {
                            "enabled": True,
                            "provider": "aihubmix",
                            "api_key": "sk-test",
                        }
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    stale_extensions = ExtensionsConfig.model_validate({"imageGeneration": {"enabled": False}})
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(config_path))
    monkeypatch.setattr("deerflow.tools.tools.get_app_config", lambda: _make_minimal_app_config(stale_extensions))
    monkeypatch.setattr("deerflow.tools.tools.is_host_bash_allowed", lambda config: True)
    reset_extensions_config()

    tools = get_available_tools(include_mcp=False)

    assert "generate_image" in {tool.name for tool in tools}
    reset_extensions_config()


def test_generate_images_calls_aihubmix_predictions_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    client_options: list[dict[str, object]] = []

    class _FakeResponse:
        headers = {"content-type": "application/json"}
        content = b""
        text = ""
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, dict[str, list[dict[str, str]]]]:
            return {"output": {"b64_json": [{"bytesBase64": base64.b64encode(PNG_BYTES).decode()}]}}

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            client_options.append(kwargs)
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def post(self, url, *, headers=None, json=None, files=None):
            calls.append({"url": url, "headers": headers, "json": json, "files": files})
            return _FakeResponse()

        def get(self, url):
            raise AssertionError(f"Unexpected download request: {url}")

    monkeypatch.setattr(image_generation_module.httpx, "Client", _FakeClient)
    config = ExtensionsConfig.model_validate(
        {
            "imageGeneration": {
                "enabled": True,
                "defaultProvider": "aihubmix",
                "providers": {
                    "aihubmix": {
                        "enabled": True,
                        "api_key": "sk-test-aihubmix",
                    }
                },
            }
        }
    )

    provider_name, metadata, images = generate_images(
        prompt="A deer drinking in the lake",
        size="1024x1024",
        quality="high",
        moderation="low",
        background="auto",
        config=config,
    )

    assert provider_name == "aihubmix"
    assert metadata.display_name == "Aihubmix"
    assert images[0].data == PNG_BYTES
    assert client_options == [{"timeout": 120.0, "trust_env": False}]
    assert calls == [
        {
            "url": "https://aihubmix.com/v1/models/openai/gpt-image-2-free/predictions",
            "headers": {"Authorization": "sk-test-aihubmix", "Content-Type": "application/json"},
            "json": {
                "input": {
                    "prompt": "A deer drinking in the lake",
                    "n": 1,
                    "size": "1024x1024",
                    "quality": "high",
                    "moderation": "low",
                    "background": "auto",
                }
            },
            "files": None,
        }
    ]


def test_generate_images_calls_minimax_image_generation_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    client_options: list[dict[str, object]] = []

    class _FakeResponse:
        headers = {"content-type": "application/json"}
        content = b""
        text = ""
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, dict[str, list[str]]]:
            return {"data": {"image_base64": [base64.b64encode(PNG_BYTES).decode()]}}

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            client_options.append(kwargs)
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def post(self, url, *, headers=None, json=None, files=None):
            calls.append({"url": url, "headers": headers, "json": json, "files": files})
            return _FakeResponse()

        def get(self, url):
            raise AssertionError(f"Unexpected download request: {url}")

    monkeypatch.setattr(image_generation_module.httpx, "Client", _FakeClient)
    config = ExtensionsConfig.model_validate(
        {
            "imageGeneration": {
                "enabled": True,
                "defaultProvider": "minimax",
                "providers": {
                    "minimax": {
                        "enabled": True,
                        "api_key": "sk-test-minimax",
                    }
                },
            }
        }
    )

    provider_name, metadata, images = generate_images(
        prompt="A person standing at Venice beach",
        size="16:9",
        config=config,
    )

    assert provider_name == "minimax"
    assert metadata.display_name == "MiniMax"
    assert images[0].data == PNG_BYTES
    assert images[0].mime_type == "image/png"
    assert client_options == [{"timeout": 120.0, "trust_env": False}]
    assert calls == [
        {
            "url": "https://api.minimaxi.com/v1/image_generation",
            "headers": {"Authorization": "Bearer sk-test-minimax", "Content-Type": "application/json"},
            "json": {
                "model": "image-01",
                "prompt": "A person standing at Venice beach",
                "response_format": "base64",
                "aspect_ratio": "16:9",
            },
            "files": None,
        }
    ]


def test_generate_images_calls_aihubmix_gemini_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class _FakeResponse:
        headers = {"content-type": "application/json"}
        content = b""
        text = ""
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, list[dict[str, dict[str, list[dict[str, dict[str, str]]]]]]]:
            return {
                "choices": [
                    {
                        "message": {
                            "multi_mod_content": [
                                {"text": "A generated tree"},
                                {
                                    "inline_data": {
                                        "mime_type": "image/png",
                                        "data": base64.b64encode(PNG_BYTES).decode(),
                                    }
                                },
                            ]
                        }
                    }
                ]
            }

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def post(self, url, *, headers=None, json=None, files=None):
            calls.append({"url": url, "headers": headers, "json": json, "files": files})
            return _FakeResponse()

        def get(self, url):
            raise AssertionError(f"Unexpected download request: {url}")

    monkeypatch.setattr(image_generation_module.httpx, "Client", _FakeClient)
    config = ExtensionsConfig.model_validate(
        {
            "imageGeneration": {
                "enabled": True,
                "defaultProvider": "aihubmix",
                "providers": {
                    "aihubmix": {
                        "enabled": True,
                        "api_key": "sk-test-aihubmix",
                        "model": "gemini-3.1-flash-image-preview-free",
                    }
                },
            }
        }
    )

    provider_name, metadata, images = generate_images(
        prompt="draw a tree",
        size="1:1",
        quality="high",
        config=config,
    )

    assert provider_name == "aihubmix"
    assert images[0].data == PNG_BYTES
    assert images[0].revised_prompt == "A generated tree"
    assert calls == [
        {
            "url": "https://aihubmix.com/gemini/v1beta/models/gemini-3.1-flash-image-preview-free:generateContent",
            "headers": {"x-goog-api-key": "sk-test-aihubmix", "Content-Type": "application/json"},
            "json": {
                "contents": [{"role": "user", "parts": [{"text": "draw a tree"}]}],
                "generationConfig": {
                    "responseModalities": ["TEXT", "IMAGE"],
                    "imageConfig": {"aspectRatio": "1:1", "imageSize": "2k"},
                },
            },
            "files": None,
        }
    ]


def test_generate_images_logs_aihubmix_remote_protocol_error(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    class _DisconnectingClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def post(self, url, *, headers=None, json=None, files=None):
            raise image_generation_module.httpx.RemoteProtocolError("Server disconnected without sending a response.")

        def get(self, url):
            raise AssertionError(f"Unexpected download request: {url}")

    monkeypatch.setattr(image_generation_module.httpx, "Client", _DisconnectingClient)
    config = ExtensionsConfig.model_validate(
        {
            "imageGeneration": {
                "enabled": True,
                "defaultProvider": "aihubmix",
                "providers": {
                    "aihubmix": {
                        "enabled": True,
                        "api_key": "sk-test-aihubmix",
                    }
                },
            }
        }
    )

    with caplog.at_level("ERROR", logger="deerflow.tools.image_generation"):
        with pytest.raises(ImageGenerationProviderError) as exc_info:
            generate_images(prompt="A deer drinking in the lake", config=config)

    assert "Aihubmix request failed: RemoteProtocolError" in str(exc_info.value)
    assert "Image generation HTTP request failed" in caplog.text
    assert "sk-test-aihubmix" not in caplog.text


def test_generate_image_tool_saves_images_to_thread_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    thread_data = _make_thread_data(tmp_path)

    def _fake_generate_images(**kwargs):
        return (
            "openai",
            SimpleNamespace(display_name="OpenAI"),
            [GeneratedImage(data=PNG_BYTES, mime_type="image/png")],
        )

    monkeypatch.setattr(generate_image_module, "generate_images", _fake_generate_images)

    result = generate_image_tool.func(
        runtime=_make_runtime(thread_data),
        prompt="A red kite over a lake",
        tool_call_id="tc-image",
    )

    assert "Generated 1 image" in _message_content(result)
    artifact = result.update["artifacts"][0]
    assert artifact.startswith("/mnt/user-data/outputs/generated-images/")
    saved_path = Path(thread_data["outputs_path"]) / artifact.removeprefix("/mnt/user-data/outputs/")
    assert saved_path.read_bytes() == PNG_BYTES


def test_generate_image_tool_reports_config_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    thread_data = _make_thread_data(tmp_path)

    def _fake_generate_images(**kwargs):
        raise ImageGenerationConfigError("Ask the user to choose an image provider.")

    monkeypatch.setattr(generate_image_module, "generate_images", _fake_generate_images)

    result = generate_image_tool.func(
        runtime=_make_runtime(thread_data),
        prompt="A red kite",
        tool_call_id="tc-image-error",
    )

    assert _message_content(result) == "Error: Ask the user to choose an image provider."
    assert "artifacts" not in result.update


def test_image_generation_api_key_roundtrip_preserves_masked_secret() -> None:
    incoming = ImageGenerationProviderConfigUpdate(
        enabled=True,
        provider="openai",
        api_key="***",
        base_url="https://api.openai.com/v1",
        model="gpt-image-1",
    )

    raw = _provider_update_to_raw(
        "openai",
        incoming,
        {"openai": {"api_key": "$OPENAI_API_KEY"}},
    )

    assert raw["api_key"] == "$OPENAI_API_KEY"


def test_image_generation_api_key_roundtrip_can_replace_secret() -> None:
    incoming = ImageGenerationProviderConfigUpdate(
        enabled=True,
        provider="openai",
        api_key="sk-new",
        base_url="https://api.openai.com/v1",
        model="gpt-image-1",
    )

    raw = _provider_update_to_raw(
        "openai",
        incoming,
        {"openai": {"api_key": "$OPENAI_API_KEY"}},
    )

    assert raw["api_key"] == "sk-new"


def test_image_generation_write_path_falls_back_to_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ExtensionsConfig, "resolve_config_path", lambda config_path=None: None)

    assert _extensions_config_path_for_write() == Path(__file__).resolve().parents[2] / "extensions_config.json"


@pytest.mark.asyncio
async def test_image_generation_update_persists_enabled_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "extensions_config.json"
    config_path.write_text(json.dumps({"mcpServers": {}, "skills": {}}), encoding="utf-8")
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(config_path))
    reset_extensions_config()

    request = ImageGenerationConfigUpdateRequest.model_validate(
        {
            "enabled": True,
            "default_provider": "aihubmix",
            "output_subdir": "generated-images",
            "providers": {
                "aihubmix": {
                    "enabled": True,
                    "provider": "aihubmix",
                    "display_name": "Aihubmix",
                    "api_key": "$AIHUBMIX_API_KEY",
                    "base_url": "https://aihubmix.com/v1",
                    "model": "openai/gpt-image-2-free",
                    "timeout_seconds": 120,
                    "params": {},
                }
            },
        }
    )

    response = await update_image_generation_configuration(request)
    persisted = ExtensionsConfig.from_file(str(config_path))

    assert response.enabled is True
    assert response.default_provider == "aihubmix"
    assert response.providers["aihubmix"].enabled is True
    assert persisted.image_generation.enabled is True
    assert persisted.image_generation.default_provider == "aihubmix"
    assert persisted.image_generation.providers["aihubmix"].enabled is True

    reset_extensions_config()
