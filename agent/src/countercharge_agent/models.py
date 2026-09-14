"""Model provider selection (env-driven): ``MODEL_PROVIDER=venice|openrouter|bedrock``.

``venice`` is the default for local/dev use. Venice requires
``venice_parameters.include_venice_system_prompt=false`` in the request body, which
strands' `OpenAIModel` passes through via ``params.extra_body`` (checked against the
installed strands 1.55.1 source: ``OpenAIModel.format_request`` merges
``self.config["params"]`` verbatim into the OpenAI chat-completions request body).
"""

from __future__ import annotations

import os
from typing import Literal

from strands.models.bedrock import BedrockModel
from strands.models.model import Model
from strands.models.openai import OpenAIModel

Role = Literal["reasoning", "vision"]

VENICE_BASE_URL = "https://api.venice.ai/api/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_DEFAULT_MODEL_ID = {
    "venice": "claude-sonnet-5",
    "openrouter": "anthropic/claude-sonnet-5",
    "bedrock": "us.anthropic.claude-sonnet-5",
}


def _model_id(prefix: str, role: Role) -> str:
    """``<PREFIX>_MODEL``, with an optional ``<PREFIX>_MODEL_VISION`` override for role="vision"."""
    if role == "vision":
        override = os.environ.get(f"{prefix}_MODEL_VISION")
        if override:
            return override
    return os.environ.get(f"{prefix}_MODEL", _DEFAULT_MODEL_ID[prefix.lower()])


def build_model(role: Role = "reasoning") -> Model:
    """Build the chat model for ``role`` from the ``MODEL_PROVIDER`` env var (default ``venice``)."""
    provider = os.environ.get("MODEL_PROVIDER", "venice").strip().lower()

    if provider == "venice":
        return OpenAIModel(
            client_args={
                "api_key": os.environ["VENICE_API_KEY"],
                "base_url": VENICE_BASE_URL,
            },
            model_id=_model_id("VENICE", role),
            params={"extra_body": {"venice_parameters": {"include_venice_system_prompt": False}}},
        )

    if provider == "openrouter":
        return OpenAIModel(
            client_args={
                "api_key": os.environ["OPENROUTER_API_KEY"],
                "base_url": OPENROUTER_BASE_URL,
            },
            model_id=_model_id("OPENROUTER", role),
        )

    if provider == "bedrock":
        # BedrockModel's __init__ is keyword-only (`def __init__(self, *, ...)`); model_id
        # must be passed by keyword, not positionally.
        return BedrockModel(model_id=_model_id("BEDROCK", role))

    raise ValueError(f"unknown MODEL_PROVIDER: {provider!r}")
