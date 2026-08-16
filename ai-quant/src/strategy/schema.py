"""策略数据结构定义"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional


class StrategyType(str, Enum):
    MOMENTUM = "momentum"
    VALUE = "value"
    EVENT_DRIVEN = "event"
    TECHNICAL = "technical"
    SENTIMENT = "sentiment"
    HYBRID = "hybrid"


class ConditionOperator(str, Enum):
    GT = ">"
    LT = "<"
    GTE = ">="
    LTE = "<="
    EQ = "=="
    BETWEEN = "BETWEEN"
    CROSS_ABOVE = "CROSS_ABOVE"
    CROSS_BELOW = "CROSS_BELOW"


class StrategyStatus(str, Enum):
    PENDING = "pending"
    VALIDATING = "validating"
    ACTIVE = "active"
    PAUSED = "paused"
    ELIMINATED = "eliminated"
    ARCHIVED = "archived"


class RebalanceFreq(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass
class FactorCondition:
    factor: str
    operator: ConditionOperator
    value: Any
    rationale: str = ""
    weight: float = 1.0

    def to_dict(self) -> dict:
        return {
            "factor": self.factor,
            "operator": self.operator.value if isinstance(self.operator, ConditionOperator) else self.operator,
            "value": self.value,
            "rationale": self.rationale,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FactorCondition":
        return cls(
            factor=d.get("factor", ""),
            operator=ConditionOperator(d["operator"]) if d.get("operator") in ConditionOperator._value2member_map_ else d.get("operator", ">"),
            value=d.get("value"),
            rationale=d.get("rationale", ""),
            weight=d.get("weight", 1.0),
        )


@dataclass
class RankingFactor:
    factor: str
    weight: float = 1.0
    direction: Literal["ascending", "descending"] = "descending"

    def to_dict(self) -> dict:
        return {"factor": self.factor, "weight": self.weight, "direction": self.direction}

    @classmethod
    def from_dict(cls, d: dict) -> "RankingFactor":
        return cls(
            factor=d.get("factor", ""),
            weight=d.get("weight", 1.0),
            direction=d.get("direction", "descending"),
        )


@dataclass
class Strategy:
    id: str = ""
    name: str = ""
    type: StrategyType = StrategyType.HYBRID
    version: int = 1
    status: StrategyStatus = StrategyStatus.PENDING
    description: str = ""
    core_hypothesis: str = ""
    logic_explanation: str = ""
    expected_market_regime: str = ""
    risk_warning: str = ""
    conditions: List[FactorCondition] = field(default_factory=list)
    ranking_factors: List[RankingFactor] = field(default_factory=list)
    max_position: int = 10
    single_stock_weight: float = 0.1
    stop_loss: float = -0.08
    stop_profit: float = 0.30
    rebalance_freq: RebalanceFreq = RebalanceFreq.WEEKLY
    created_at: str = ""
    parent_strategy_id: Optional[str] = None
    generation: int = 0
    generation_method: str = "generate"
    performance: Optional[Dict] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type.value if isinstance(self.type, StrategyType) else self.type,
            "version": self.version,
            "status": self.status.value if isinstance(self.status, StrategyStatus) else self.status,
            "description": self.description,
            "core_hypothesis": self.core_hypothesis,
            "logic_explanation": self.logic_explanation,
            "expected_market_regime": self.expected_market_regime,
            "risk_warning": self.risk_warning,
            "conditions": [c.to_dict() for c in self.conditions],
            "ranking_factors": [r.to_dict() for r in self.ranking_factors],
            "max_position": self.max_position,
            "single_stock_weight": self.single_stock_weight,
            "stop_loss": self.stop_loss,
            "stop_profit": self.stop_profit,
            "rebalance_freq": self.rebalance_freq.value
            if isinstance(self.rebalance_freq, RebalanceFreq)
            else self.rebalance_freq,
            "created_at": self.created_at,
            "parent_strategy_id": self.parent_strategy_id,
            "generation": self.generation,
            "generation_method": self.generation_method,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Strategy":
        if "id" not in d or not d["id"]:
            d["id"] = f"st_{uuid.uuid4().hex[:12]}"
        if "created_at" not in d or not d["created_at"]:
            d["created_at"] = datetime.now().isoformat()
        s = cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            type=StrategyType(d["type"]) if d.get("type") in StrategyType._value2member_map_ else StrategyType.HYBRID,
            version=d.get("version", 1),
            status=StrategyStatus(d["status"]) if d.get("status") in StrategyStatus._value2member_map_ else StrategyStatus.PENDING,
            description=d.get("description", ""),
            core_hypothesis=d.get("core_hypothesis", ""),
            logic_explanation=d.get("logic_explanation", ""),
            expected_market_regime=d.get("expected_market_regime", ""),
            risk_warning=d.get("risk_warning", ""),
            conditions=[FactorCondition.from_dict(c) for c in d.get("conditions", [])],
            ranking_factors=[RankingFactor.from_dict(r) for r in d.get("ranking_factors", [])],
            max_position=int(d.get("max_position", 10)),
            single_stock_weight=float(d.get("single_stock_weight", 0.1)),
            stop_loss=float(d.get("stop_loss", -0.08)),
            stop_profit=float(d.get("stop_profit", 0.30)),
            rebalance_freq=RebalanceFreq(d["rebalance_freq"])
            if d.get("rebalance_freq") in RebalanceFreq._value2member_map_
            else RebalanceFreq.WEEKLY,
            created_at=d.get("created_at", ""),
            parent_strategy_id=d.get("parent_strategy_id"),
            generation=int(d.get("generation", 0)),
            generation_method=d.get("generation_method", "generate"),
            performance=d.get("performance"),
        )
        return s

    def validate(self) -> tuple[bool, List[str]]:
        errors = []
        if not self.name:
            errors.append("策略名称不能为空")
        if not self.core_hypothesis:
            errors.append("缺少核心假设")
        if len(self.conditions) == 0:
            errors.append("至少需要一个筛选条件")
        elif len(self.conditions) > 8:
            errors.append(f"条件过多({len(self.conditions)}>8)")
        if self.stop_loss >= 0:
            errors.append("止损线应为负数")
        if self.stop_profit <= 0:
            errors.append("止盈线应为正数")
        if self.max_position <= 0:
            errors.append("持仓数量应为正整数")
        return len(errors) == 0, errors
