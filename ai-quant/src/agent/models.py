"""交易 Agent 数据模型"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# 内置统筹 Agent 固定 id
OVERSEER_AGENT_ID = "system_overseer"

# 统筹 Agent 基础提示词（全局把控，不参与技能体系）
OVERSEER_PROMPT = """你是「统筹总管」，A股量化模拟交易系统的全局统筹 Agent。
职责：
1. 汇总对比所有交易 Agent 的表现（收益、持仓、风格），给出全局判断。
2. 帮助每个 Agent 进行模拟交易：提醒调仓、提示机会、控制风险。
3. 用数据说话，先调用工具（get_ranking / get_market_overview / 各 Agent 持仓）再下结论。
4. 全部用简体中文回复，专业、简洁、可执行。
"""


@dataclass
class Agent:
    """交易 Agent 实体（含独立 LLM 配置与模拟账户参数）"""

    id: str
    name: str
    description: str = ""
    system_prompt: str = ""
    llm_provider: str = "deepseek"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    status: str = "running"  # running/paused/archived
    is_overseer: bool = False
    initial_capital: float = 100000.0
    current_cash: float = 100000.0
    max_position: int = 10
    single_stock_weight: float = 0.10
    commission_rate: float = 0.0003
    slippage: float = 0.001
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_active_at: datetime | None = None

    @property
    def llm_configured(self) -> bool:
        """是否配置了可用的 LLM（有 api_key 或 base_url/model 显式配置）"""
        return bool(self.llm_api_key) or bool(self.llm_base_url) or bool(self.llm_model)


@dataclass
class Position:
    """模拟持仓"""

    agent_id: str
    ts_code: str
    shares: int
    avg_cost: float
    current_price: float

    @property
    def market_value(self) -> float:
        return self.shares * self.current_price

    @property
    def pnl_pct(self) -> float:
        if self.avg_cost <= 0:
            return 0.0
        return self.current_price / self.avg_cost - 1


@dataclass
class Trade:
    """模拟成交"""

    agent_id: str
    ts_code: str
    direction: str  # buy/sell
    price: float
    shares: int
    trade_date: str
    reason: str = ""


@dataclass
class MemoryItem:
    """Agent 长期记忆条目"""

    id: int = 0
    agent_id: str = ""
    content: str = ""
    memory_type: str = "experience"  # instruction/experience/chat_summary
    created_at: datetime | None = None


@dataclass
class AgentTask:
    """Agent 定时自动任务"""

    id: int = 0
    agent_id: str = ""
    schedule_type: str = "daily"  # daily/interval
    schedule_time: str = "09:30"  # HH:MM
    interval_hours: float = 0.0
    enabled: bool = True
    last_run_at: datetime | None = None
    created_at: datetime | None = None
