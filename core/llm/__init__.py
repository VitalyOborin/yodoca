"""Core LLM module: multi-provider model routing. Not extensible by extensions."""

from core.llm.capabilities import EmbeddingCapability
from core.llm.protocol import (
    ModelConfig,
    ModelProvider,
    ModelRouterProtocol,
    ProviderConfig,
)
from core.llm.router import ModelRouter

__all__ = [
    "EmbeddingCapability",
    "ModelConfig",
    "ModelProvider",
    "ModelRouter",
    "ModelRouterProtocol",
    "ProviderConfig",
]
