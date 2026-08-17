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
        return self.chat_completion(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        ).content

    def _create(self, messages, temperature, max_tokens, tools, json_mode):
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens or config.llm.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        return kwargs

    def chat_completion(self, messages, temperature=0.7, max_tokens=None, tools=None, json_mode=False):
        """多轮消息 + 原生工具调用（OpenAI 兼容 function calling）"""
        if not self.available:
            raise RuntimeError(f"Provider {self.name} 未配置 API Key")
        kwargs = self._create(messages, temperature, max_tokens, tools, json_mode)
        for attempt in range(3):
            try:
                resp = self._client.chat.completions.create(**kwargs)
                msg = resp.choices[0].message
                tool_calls = None
                if getattr(msg, "tool_calls", None):
                    tool_calls = [
                        {
                            "id": tc.id,
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                        for tc in msg.tool_calls
                    ]
                content = msg.content or ""
                return _ChatMessage(
                    content=content,
                    tool_calls=tool_calls,
                    reasoning_content=getattr(msg, "reasoning_content", None),
                )
            except Exception as e:
                logger.warning(f"{self.name} 调用失败 (attempt {attempt+1}): {e}")
                if attempt < 2:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"{self.name} 调用失败")

    def chat_stream(self, messages, temperature=0.7, max_tokens=None, tools=None):
        """流式对话：逐段 yield 文本增量；工具调用首轮也做流式累积后返回"""
        if not self.available:
            raise RuntimeError(f"Provider {self.name} 未配置 API Key")
        kwargs = self._create(messages, temperature, max_tokens, tools, json_mode=False)
        kwargs["stream"] = True
        full = []
        pending_tools: dict[int, dict] = {}
        try:
            stream = self._client.chat.completions.create(**kwargs)
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                if getattr(delta, "tool_calls", None):
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in pending_tools:
                            pending_tools[idx] = {"id": tc.id or "", "name": "", "arguments": ""}
                        if tc.id:
                            pending_tools[idx]["id"] = tc.id
                        if tc.function and tc.function.name:
                            pending_tools[idx]["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            pending_tools[idx]["arguments"] += tc.function.arguments
                    continue
                if delta.content:
                    full.append(delta.content)
                    yield delta.content
        finally:
            pass
        if pending_tools:
            # 流式工具调用结果：按 tc.index 排序后挂到客户端上供上层读取
            self._pending_tool_calls = [
                {"id": pending_tools[i]["id"], "name": pending_tools[i]["name"],
                 "arguments": pending_tools[i]["arguments"]}
                for i in sorted(pending_tools)
            ]
        else:
            self._pending_tool_calls = None


class _ChatMessage:
    """轻量消息对象，兼容 content / tool_calls / reasoning_content 访问"""

    def __init__(self, content="", tool_calls=None, reasoning_content=None):
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning_content = reasoning_content


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
