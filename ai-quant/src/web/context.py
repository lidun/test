"""全局应用上下文：统一持有系统各组件单例

避免模块间循环依赖，web/调度器/CLI 共享同一组组件实例。
"""
from __future__ import annotations

from typing import Optional

from loguru import logger


class AppContext:
    _instance: Optional["AppContext"] = None

    def __init__(self):
        self.data_pipeline = None
        self.vector_store = None
        self.knowledge_retriever = None
        self.generator = None
        self.compiler = None
        self.validator = None
        self.arena = None
        self.evolution = None
        self.metrics = None
        self.alert = None
        self.reporter = None
        self.initialized = False

    @classmethod
    def get(cls) -> "AppContext":
        if cls._instance is None:
            cls._instance = AppContext()
        return cls._instance

    def init(self, with_knowledge: bool = True):
        """初始化所有组件"""
        from src.core.database import init_database
        from src.data.data_pipeline import DataPipeline
        from src.strategy.generator import LLMStrategyGenerator
        from src.strategy.compiler import StrategyCompiler
        from src.strategy.validator import QuickValidator
        from src.arena.simulator import SimulationEngine
        from src.evolution.engine import EvolutionEngine
        from src.monitor.metrics import MetricsCollector
        from src.monitor.alert import AlertManager
        from src.monitor.reporter import ReportGenerator

        if self.initialized:
            return self

        init_database()
        self.data_pipeline = DataPipeline()

        if with_knowledge:
            from src.knowledge.vector_store import VectorStoreManager
            from src.knowledge.retriever import KnowledgeRetriever
            from src.knowledge.knowledge_seeder import seed_knowledge_if_empty

            self.vector_store = VectorStoreManager()
            seed_knowledge_if_empty(self.vector_store)
            self.knowledge_retriever = KnowledgeRetriever(self.vector_store)

        self.generator = LLMStrategyGenerator(self.knowledge_retriever)
        self.compiler = StrategyCompiler()
        self.validator = QuickValidator()
        self.arena = SimulationEngine()
        self.evolution = EvolutionEngine(
            self.generator, self.compiler, self.validator, self.arena, self.knowledge_retriever
        )
        self.metrics = MetricsCollector(self.arena)
        self.alert = AlertManager()
        self.reporter = ReportGenerator()
        self.initialized = True

        # 无真实数据时生成演示行情，确保系统开箱可用
        try:
            from src.data.demo_data import ensure_demo_data

            ensure_demo_data()
        except Exception as e:
            logger.warning(f"演示数据初始化失败: {e}")

        logger.info("系统组件初始化完成")
        return self
