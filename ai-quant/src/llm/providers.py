"""LLM Provider 实现

国内主流模型均提供 OpenAI 兼容接口，统一通过 openai 库驱动。
支持：DeepSeek / 通义千问 / Moonshot / 智谱GLM / 百度千帆 / 本地Ollama / OpenAI / Anthropic
"""
from __future__ import annotations

import time
from typing import Optional

from loguru import logger

from src.core.config import ProviderConfig, config
from src.llm.base import LLMClient


class OpenAICompatibleClient(LLMClient):
    """OpenAI 兼容协议客户端（覆盖绝大多数国产模型）"""

    def __init__(self, name: str, provider_cfg: ProviderConfig, timeout: int = 120):
        self.name = name
        self._cfg = provider_cfg
        self.model = provider_cfg.model
        self._client = None
        if provider_cfg.available:
            try:
                from openai import OpenAI

                self._client = OpenAI(
                    api_key=provider_cfg.api_key,
                    base_url=provider_cfg.base_url or None,
                    timeout=timeout,
                )
            except ImportError:
                logger.error("openai 库未安装，无法初始化 LLM 客户端")

    @property
    def available(self) -> bool:
        return self._client is not None

    def chat(self, system_prompt, user_prompt, temperature=0.7, max_tokens=None, json_mode=False) -> str:
        if not self.available:
            raise RuntimeError(f"Provider {self.name} 未配置 API Key")
        kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens or config.llm.max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        for attempt in range(3):
            try:
                resp = self._client.chat.completions.create(**kwargs)
                return resp.choices[0].message.content or ""
            except Exception as e:
                logger.warning(f"{self.name} 调用失败 (attempt {attempt+1}): {e}")
                if attempt < 2:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"{self.name} 调用失败")


class AnthropicClient(LLMClient):
    """Anthropic Claude 客户端"""

    def __init__(self, api_key: str, model: str, timeout: int = 120):
        self.name = "anthropic"
        self.model = model
        self._client = None
        if api_key:
            try:
                from anthropic import Anthropic

                self._client = Anthropic(api_key=api_key, timeout=timeout)
            except ImportError:
                logger.error("anthropic 库未安装")

    @property
    def available(self) -> bool:
        return self._client is not None

    def chat(self, system_prompt, user_prompt, temperature=0.7, max_tokens=None, json_mode=False) -> str:
        if not self.available:
            raise RuntimeError("Anthropic 未配置 API Key")
        resp = self._client.messages.create(
            model=self.model,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=temperature,
            max_tokens=max_tokens or config.llm.max_tokens,
        )
        return "".join(block.text for block in resp.content if block.type == "text")
