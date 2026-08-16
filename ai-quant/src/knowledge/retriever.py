"""知识检索器：为策略生成/进化提供相关知识"""
from __future__ import annotations

from typing import Optional

from loguru import logger

from src.knowledge.vector_store import VectorStoreManager


class KnowledgeRetriever:
    def __init__(self, vector_store: VectorStoreManager):
        self.vs = vector_store

    def retrieve_for_strategy_generation(
        self,
        market_context: Optional[dict] = None,
        strategy_type: Optional[str] = None,
        n_results: int = 5,
    ) -> str:
        """为策略生成检索组合知识"""
        market_context = market_context or {}
        trend = market_context.get("trend", "震荡")
        hot_sectors = market_context.get("hot_sectors", [])

        query_parts = [f"在当前{trend}市环境下选择股票策略"]
        if hot_sectors:
            query_parts.append(f"关注行业：{','.join(hot_sectors)}")
        if strategy_type:
            type_keyword = {
                "momentum": "动量",
                "value": "价值",
                "technical": "技术指标",
                "event": "事件驱动",
                "sentiment": "情绪",
                "hybrid": "多因子",
            }.get(strategy_type, strategy_type)
            query_parts.append(f"因子类型：{type_keyword}")

        query = "。".join(query_parts)
        names = ["factor_research", "strategy_patterns", "market_regimes", "academic_papers"]
        if strategy_type == "technical":
            names.append("trading_rules")
        return self.vs.query_combined(query, n_results=n_results)

    def retrieve_for_mutation(self, strategy_meta: dict, performance: dict) -> str:
        """为策略变异检索改进建议"""
        query = f"改进策略：{strategy_meta.get('name', '')}，当前收益{performance.get('total_return', 0):.2%}，最大回撤{performance.get('max_drawdown', 0):.2%}"
        return self.vs.query_combined(query, n_results=4)

    def retrieve_market_knowledge(self, market_context: dict) -> str:
        query = f"市场环境：{market_context.get('trend', '震荡')}，波动率{market_context.get('volatility', '中等')}"
        return self.vs.query_combined(query, n_results=3, collection_names=["market_regimes"])

    def retrieve_for_analysis(self, strategy_meta: dict) -> str:
        query = f"分析策略风险：{strategy_meta.get('name', '')}，核心逻辑{strategy_meta.get('core_hypothesis', '')}"
        return self.vs.query_combined(
            query, n_results=4, collection_names=["risk_management", "trading_rules"]
        )
