"""LLM 客户端工厂：根据配置创建默认客户端

配置来源优先级：system_config 表（运行时保存，即时生效）> .env > 默认值。
"""
from __future__ import annotations

from loguru import logger

from src.core.config import ProviderConfig, config
from src.core.config_store import get_llm_overrides
from src.llm.base import LLMClient, MockLLMClient
from src.llm.providers import AnthropicClient, OpenAICompatibleClient

_PROVIDER_LABELS = {
    "deepseek": "DeepSeek",
    "qwen": "通义千问",
    "moonshot": "Moonshot",
    "glm": "智谱GLM",
    "ollama": "本地Ollama",
    "openai": "OpenAI",
}


def _apply_overrides(name: str) -> tuple[str, str, str] | None:
    """从 system_config 读取指定 provider 的覆盖配置（api_key/base_url/model）"""
    overrides = get_llm_overrides()
    p = overrides.get(name)
    if not p or not isinstance(p, dict):
        return None
    return p.get("api_key", ""), p.get("base_url", ""), p.get("model", "")


def create_client(provider_name: str | None = None) -> LLMClient:
    """根据 provider 名称创建客户端

    Args:
        provider_name: deepseek/qwen/moonshot/glm/baidu/ollama/openai/anthropic
                       None 时使用配置的默认 provider
    """
    default_provider = provider_name or config.llm.default_provider
    overrides = get_llm_overrides()
    name = provider_name or overrides.get("_default_provider") or default_provider

    if name == "anthropic":
        if config.llm.anthropic_api_key:
            return AnthropicClient(config.llm.anthropic_api_key, config.llm.anthropic_model)
        return MockLLMClient()

    if name in config.llm.providers:
        provider_cfg = config.llm.providers[name]
        db_cfg = _apply_overrides(name)
        if db_cfg:
            api_key, base_url, model = db_cfg
            if api_key or model:
                provider_cfg = ProviderConfig(name)
                provider_cfg.api_key = api_key
                provider_cfg.base_url = base_url
                provider_cfg.model = model
        if not provider_cfg.available:
            logger.warning(
                f"Provider {name} 未配置 API Key，使用 mock 客户端。"
                f"可用 provider: {config.llm.available_providers() or '无'}"
            )
            return MockLLMClient()
        return OpenAICompatibleClient(name, provider_cfg, timeout=config.llm.request_timeout)

    logger.warning(f"未知 provider: {name}，使用 mock 客户端")
    return MockLLMClient()
