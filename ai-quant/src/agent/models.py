"""交易 Agent 数据模型"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# 内置统筹 Agent 固定 id
OVERSEER_AGENT_ID = "system_overseer"


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
    skills: str = ""  # 逗号分隔的技能集
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

    @property
    def skill_list(self) -> list[str]:
        """解析后的技能列表"""
        return [s.strip() for s in self.skills.split(",") if s.strip()]


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
