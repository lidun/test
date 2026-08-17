"""LLM 客户端抽象"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from loguru import logger


class LLMClient(ABC):
    name = "base"
    model = ""

    @property
    def supports_tools(self) -> bool:
        """是否支持原生 function calling（OpenAI 兼容 tools 参数）"""
        return True

    @abstractmethod
    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
    ) -> str:
        """调用对话补全，返回文本内容"""
        raise NotImplementedError

    def chat_completion(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        tools: Optional[list[dict]] = None,
        json_mode: bool = False,
    ) -> Any:
        """多轮消息 + 工具调用。返回对象含 .content 与 .tool_calls。

        默认实现降级为单轮 chat（子类可覆写以支持原生 tools）。
        """
        system_prompt = ""
        user_prompt = ""
        for m in messages:
            if m.get("role") == "system":
                system_prompt += m.get("content", "") + "\n"
            else:
                user_prompt += f"[{m.get('role')}]\n{m.get('content', '')}\n"
        text = self.chat(system_prompt, user_prompt, temperature, max_tokens, json_mode)

        class _Msg:
            content = text
            tool_calls = None

        return _Msg()

    def chat_stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        tools: Optional[list[dict]] = None,
    ):
        """流式对话。逐段 yield 文本增量（str）；不支持流式时一次性 yield 全部。"""
        yield self.chat_completion(messages, temperature, max_tokens, tools).content

    @property
    def available(self) -> bool:
        return True


class MockLLMClient(LLMClient):
    """无 Key 时的降级客户端：支持文本协议（输出 JSON action 触发工具）"""

    name = "mock"
    model = "mock"

    @property
    def supports_tools(self) -> bool:
        return False

    def chat(self, system_prompt, user_prompt, temperature=0.7, max_tokens=None, json_mode=False) -> str:
        return (
            '{"action": "get_portfolio", "args": {}, "text": "我是本地演示模式，暂未配置大模型Key。'
            '我帮你查看了一下当前持仓情况。"}'
        )

    def chat_completion(self, messages, temperature=0.7, max_tokens=None, tools=None, json_mode=False):
        # 降级：当作普通 chat 处理（assistant 会走文本协议解析 action）
        return super().chat_completion(messages, temperature, max_tokens, tools, json_mode)

    def chat_stream(self, messages, temperature=0.7, max_tokens=None, tools=None):
        yield self.chat_completion(messages, temperature, max_tokens, tools).content
