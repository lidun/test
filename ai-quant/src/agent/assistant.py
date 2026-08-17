"""Agent 对话助手：多轮对话 + 工具调用循环 + 流式输出

- 支持原生 function calling（DeepSeek/Qwen/Moonshot/GLM/OpenAI/Ollama）
- 不支持时降级为文本 JSON 协议
- run_stream 产出事件字典：token / tool / done / error
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Iterator

from loguru import logger

from src.agent.models import Agent
from src.agent.portfolio import AgentPortfolio
from src.agent.skills import build_skills_prompt
from src.agent.store import AgentStore
from src.agent.tools import (
    TEXT_PROTOCOL_INSTRUCTION,
    TOOLS_SCHEMA,
    dispatch,
)
from src.core.config import ProviderConfig, config
from src.llm.base import LLMClient
from src.llm.factory import create_client
from src.llm.providers import OpenAICompatibleClient

MAX_TOOL_STEPS = 8


class AgentAssistant:
    def __init__(self, store: AgentStore | None = None):
        self.store = store or AgentStore()

    # ---------------- 客户端 ----------------

    def build_client(self, agent: Agent) -> LLMClient:
        """按 Agent 独立 LLM 配置构建客户端；未配置时回退系统默认"""
        if agent.llm_configured:
            cfg = ProviderConfig("agent")
            cfg.api_key = agent.llm_api_key
            cfg.base_url = agent.llm_base_url
            cfg.model = agent.llm_model
            # 本地 Ollama 无需鉴权
            if not cfg.api_key and (
                agent.llm_provider == "ollama" or "localhost" in agent.llm_base_url
            ):
                cfg.api_key = "ollama-local"
            if cfg.api_key:
                return OpenAICompatibleClient(
                    agent.llm_provider, cfg, timeout=config.llm.request_timeout
                )
            logger.warning(f"Agent {agent.name} LLM 未配置完整 Key，回退系统默认客户端")
        return create_client()

    # ---------------- 提示词 ----------------

    def build_system_prompt(self, agent: Agent, portfolio: AgentPortfolio, supports_tools: bool) -> str:
        mem = self.store.recall_memories(agent.id, limit=10)
        account = portfolio.summary()
        pos_lines = "\n".join(
            f"- {p['name']}({p['ts_code']}) {p['shares']}股 成本{p['avg_cost']} "
            f"现价{p['current_price']} 盈亏{p['pnl_pct']*100:.2f}%"
            for p in account["positions"]
        ) or "- 空仓"
        mem_lines = "\n".join(f"- {m.content}" for m in mem) or "- 暂无长期记忆"
        # 文件区记忆（memory.md 全文，双写备份）
        file_mem = self.store.file_store.read_memory(agent.id, limit_chars=2000)
        if file_mem:
            mem_lines = mem_lines + "\n（文件记忆归档：）\n" + file_mem
        extra = "" if supports_tools else TEXT_PROTOCOL_INSTRUCTION
        # 勾选的共享技能注入
        skills_prompt = build_skills_prompt(agent.skill_list)
        skills_block = f"\n\n{skills_prompt}\n" if skills_prompt else ""
        return f"""你是「{agent.name}」交易 Agent，运行在 A 股量化模拟交易沙盒中。
当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}
定位：{agent.description or '自主选股与模拟交易'}
{agent.system_prompt or ''}
{skills_block}
—— 长期记忆（你曾记住的策略与偏好）——
{mem_lines}

—— 当前模拟账户 ——
现金 {account['cash']:.2f}，总资产 {account['total_value']:.2f}，
累计收益 {account['cumulative_return']*100:.2f}%，持仓 {account['positions_count']} 只
{pos_lines}

工作准则：
1. 决策前先调用工具查行情/市场/因子数据，用数据说话，避免凭感觉。
2. 买入/卖出必须调用 buy_stock/sell_stock 并说明理由。
3. 用户交代的策略规则、选股偏好，用 remember 写入长期记忆。
4. 若记忆或账户信息需要回忆，用 recall_memory。
5. 只做模拟交易，金额与股数以工具结果为准。
6. 全部用简体中文回复，专业、简洁、可执行。
{extra}"""

    def build_messages(self, agent: Agent, user_input: str, portfolio: AgentPortfolio,
                       supports_tools: bool) -> list[dict]:
        system = self.build_system_prompt(agent, portfolio, supports_tools)
        messages: list[dict] = [{"role": "system", "content": system}]
        # 最近对话上下文（最近 8 条）
        history = self.store.list_chat(agent.id, limit=8)
        for h in history:
            if h["role"] in ("user", "assistant"):
                messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": user_input})
        return messages

    # ---------------- 对话循环 ----------------

    def run_stream(self, agent: Agent, user_input: str, task_mode: bool = False) -> Iterator[dict]:
        """执行对话并产出事件：token/tool/done/error"""
        portfolio = AgentPortfolio(agent, self.store)
        client = self.build_client(agent)
        try:
            if not client.available:
                yield {"type": "error", "error": "未配置可用的 LLM，请先在系统配置中填写大模型 Key"}
                return
        except Exception as e:
            yield {"type": "error", "error": f"LLM 客户端初始化失败: {e}"}
            return

        self.store.add_chat(agent.id, "user", user_input)
        if not task_mode:
            self.store.touch_agent(agent.id)

        messages = self.build_messages(agent, user_input, portfolio, client.supports_tools)
        final_text = ""
        try:
            for step in range(MAX_TOOL_STEPS):
                if client.supports_tools:
                    msg = client.chat_completion(messages, temperature=0.4, tools=TOOLS_SCHEMA)
                    if msg.tool_calls:
                        for tc in msg.tool_calls:
                            name = tc["name"]
                            try:
                                args = json.loads(tc.get("arguments") or "{}")
                            except json.JSONDecodeError:
                                args = {}
                            yield {"type": "tool", "name": name, "args": args}
                            result = dispatch(portfolio, self.store, name, args)
                            yield {"type": "tool_result", "name": name, "result": result}
                            assistant_msg: dict = {
                                "role": "assistant", "content": None,
                                "tool_calls": [{"id": tc["id"], "type": "function",
                                                "function": {"name": name, "arguments": tc.get("arguments") or "{}"}}],
                            }
                            if getattr(msg, "reasoning_content", None):
                                assistant_msg["reasoning_content"] = msg.reasoning_content
                            messages.append(assistant_msg)
                            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                        continue
                    final_text = msg.content or ""
                    # 流式输出最终回复
                    for tok in client.chat_stream(messages, temperature=0.4):
                        yield {"type": "token", "text": tok}
                    break
                else:
                    # 文本协议模式：解析 JSON action
                    resp = client.chat_completion(messages, temperature=0.4)
                    text = resp.content or ""
                    action = self._parse_action(text)
                    if action:
                        name, args = action
                        yield {"type": "tool", "name": name, "args": args}
                        result = dispatch(portfolio, self.store, name, args)
                        yield {"type": "tool_result", "name": name, "result": result}
                        messages.append({"role": "user", "content": f"工具 {name} 返回: {result}"})
                        continue
                    for tok in client.chat_stream(messages, temperature=0.4):
                        yield {"type": "token", "text": tok}
                    final_text = text
                    break
            else:
                final_text = final_text or "已达到最大工具调用步数，请稍后再试。"
        except Exception as e:
            logger.error(f"Agent {agent.name} 对话失败: {e}")
            yield {"type": "error", "error": f"对话失败: {e}"}
            return

        if final_text:
            self.store.add_chat(agent.id, "assistant", final_text)
        # 更新账户估值与绩效
        try:
            summary = portfolio.mark_to_market()
        except Exception as e:
            logger.warning(f"绩效更新失败: {e}")
            summary = {}
        yield {"type": "done", "text": final_text, "summary": summary}

    def auto_run(self, agent: Agent) -> str:
        """定时自动任务：让 Agent 自主检查市场与持仓并决策"""
        prompt = (
            "现在是定时自动任务时间。请依次：1) 查看市场概况；2) 查看你的持仓与现金；"
            "3) 回顾你的策略记忆；4) 判断是否需要调仓（买/卖），如需交易调用工具执行；"
            "5) 用简短中文总结今天的操作与理由。"
        )
        last_text = ""
        for event in self.run_stream(agent, prompt, task_mode=True):
            if event["type"] == "error":
                last_text = event["error"]
            elif event["type"] == "done":
                last_text = event["text"]
        return last_text

    @staticmethod
    def _parse_action(text: str) -> tuple[str, dict] | None:
        """从回复中解析文本协议的 JSON action 块"""
        for m in re.finditer(r"\{[^{}]*\"action\"[^{}]*\}", text):
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict) and data.get("action"):
                    return str(data["action"]), data.get("args") or {}
            except json.JSONDecodeError:
                continue
        return None
