"""LLM 客户端抽象"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class LLMClient(ABC):
    name = "base"
    model = ""

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

    @property
    def available(self) -> bool:
        return True


class MockLLMClient(LLMClient):
    """无 Key 时的降级客户端，返回固定占位响应"""

    name = "mock"
    model = "mock"

    def chat(self, system_prompt, user_prompt, temperature=0.7, max_tokens=None, json_mode=False) -> str:
        return '{"strategies": []}'
