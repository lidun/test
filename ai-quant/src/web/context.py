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
        self.store = None          # AgentStore
        self.assistant = None      # AgentAssistant
        self.task_scheduler = None # AgentTaskScheduler
        self.metrics = None
        self.alert = None
        self.initialized = False

    @classmethod
    def get(cls) -> "AppContext":
        if cls._instance is None:
            cls._instance = AppContext()
        return cls._instance

    def init(self, with_knowledge: bool = True):
        """初始化所有组件"""
        from src.agent.assistant import AgentAssistant
        from src.agent.scheduler import AgentTaskScheduler
        from src.agent.store import AgentStore
        from src.core.database import init_database
        from src.data.data_pipeline import DataPipeline
        from src.monitor.alert import AlertManager
        from src.monitor.metrics import MetricsCollector

        if self.initialized:
            return self

        init_database()
        self.data_pipeline = DataPipeline()
        self.store = AgentStore()
        self.assistant = AgentAssistant(self.store)
        self.task_scheduler = AgentTaskScheduler(self.assistant, self.store, scheduler=None)
        self.metrics = MetricsCollector()
        self.alert = AlertManager()
        self.initialized = True

        # 无真实数据时生成演示行情，确保系统开箱可用
        try:
            from src.data.demo_data import ensure_demo_data

            ensure_demo_data()
        except Exception as e:
            logger.warning(f"演示数据初始化失败: {e}")

        logger.info("系统组件初始化完成（Agent 体系）")
        return self
