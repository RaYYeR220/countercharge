import pytest
from strands.models.bedrock import BedrockModel
from strands.models.openai import OpenAIModel

from countercharge_agent.models import build_model


def test_venice_is_default_provider(monkeypatch):
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    monkeypatch.setenv("VENICE_API_KEY", "test-key")

    model = build_model()

    assert isinstance(model, OpenAIModel)
    assert model.client_args["api_key"] == "test-key"
    assert model.client_args["base_url"] == "https://api.venice.ai/api/v1"
    assert model.config["model_id"] == "claude-sonnet-5"
    assert model.config["params"]["extra_body"]["venice_parameters"]["include_venice_system_prompt"] is False


def test_venice_model_id_override(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "venice")
    monkeypatch.setenv("VENICE_API_KEY", "test-key")
    monkeypatch.setenv("VENICE_MODEL", "some-other-model")

    model = build_model()

    assert model.config["model_id"] == "some-other-model"


def test_venice_vision_role_override(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "venice")
    monkeypatch.setenv("VENICE_API_KEY", "test-key")
    monkeypatch.setenv("VENICE_MODEL", "reasoning-model")
    monkeypatch.setenv("VENICE_MODEL_VISION", "vision-model")

    reasoning = build_model(role="reasoning")
    vision = build_model(role="vision")

    assert reasoning.config["model_id"] == "reasoning-model"
    assert vision.config["model_id"] == "vision-model"


def test_openrouter_provider(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")

    model = build_model()

    assert isinstance(model, OpenAIModel)
    assert model.client_args["base_url"] == "https://openrouter.ai/api/v1"
    assert model.config["model_id"] == "anthropic/claude-sonnet-5"


def test_bedrock_provider_uses_keyword_model_id(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "bedrock")
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)

    model = build_model()

    assert isinstance(model, BedrockModel)
    assert model.config["model_id"] == "us.anthropic.claude-sonnet-5"


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "not-a-real-provider")

    with pytest.raises(ValueError, match="unknown MODEL_PROVIDER"):
        build_model()
