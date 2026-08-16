"""LLM 客户端工厂：根据配置创建默认客户端"""
from __future__ import annotations

from loguru import logger

from src.core.config import config
from src.llm.base import LLMClient, MockLLMClient
from src.llm.providers import AnthropicClient, OpenAICompatibleClient


def create_client(provider_name: str | None = None) -> LLMClient:
    """根据 provider 名称创建客户端

    Args:
        provider_name: deepseek/qwen/moonshot/glm/baidu/ollama/openai/anthropic
                       None 时使用配置的默认 provider
    """
    name = provider_name or config.llm.default_provider

    if name == "anthropic":
        if config.llm.anthropic_api_key:
            return AnthropicClient(config.llm.anthropic_api_key, config.llm.anthropic_model)
        return MockLLMClient()

    if name in config.llm.providers:
        provider_cfg = config.llm.providers[name]
        if not provider_cfg.available:
            logger.warning(
                f"Provider {name} 未配置 API Key，使用 mock 客户端。"
                f"可用 provider: {config.llm.available_providers() or '无'}"
            )
            return MockLLMClient()
        return OpenAICompatibleClient(name, provider_cfg, timeout=config.llm.request_timeout)

    logger.warning(f"未知 provider: {name}，使用 mock 客户端")
    return MockLLMClient()
