"""定时任务：每日数据更新、Agent 自动任务、告警、健康检查"""
from __future__ import annotations

import asyncio

from loguru import logger

from src.web.context import AppContext


class ScheduledJobs:
    def __init__(self, context: AppContext):
        self.ctx = context

    @property
    def data_pipeline(self):
        return self.ctx.data_pipeline

    @property
    def store(self):
        return self.ctx.store

    @property
    def metrics(self):
        return self.ctx.metrics

    @property
    def alert(self):
        return self.ctx.alert

    async def daily_data_update(self):
        """每日数据更新（默认 15:30）"""
        logger.info("[定时] 每日数据更新")
        try:
            await self.data_pipeline.daily_update()
        except Exception as e:
            logger.error(f"[定时] 每日数据更新失败: {e}")

    async def daily_agent_mark(self):
        """每日收盘后为所有 Agent 更新估值与绩效（默认 16:00）"""
        from src.agent.portfolio import AgentPortfolio

        logger.info("[定时] Agent 账户估值")
        for agent in self.store.list_agents():
            if agent.status != "running":
                continue
            try:
                await asyncio.to_thread(AgentPortfolio(agent, self.store).mark_to_market)
            except Exception as e:
                logger.warning(f"[定时] Agent {agent.name} 估值失败: {e}")

    async def daily_alerts_check(self):
        """每日告警检查（默认 16:30）"""
        logger.info("[定时] 每日告警检查")
        alerts = self.metrics.check_strategy_alerts()
        system_metrics = self.metrics.collect_system_metrics()
        self.alert.check_and_alert(alerts, system_metrics)

    async def system_health_check(self):
        """系统健康检查（每小时）"""
        health = self.metrics.health_check()
        if health["status"] != "healthy":
            self.alert.send_system_alert(f"系统状态: {health['status']}", "warning")

    async def cleanup_old_data(self):
        """数据清理（凌晨3点）：保留最近2年行情，清理过期缓存"""
        from datetime import date, timedelta

        from sqlalchemy import text

        from src.core.database import get_db_session

        cutoff = date.today() - timedelta(days=365 * 2)
        with get_db_session() as session:
            deleted = session.execute(
                text("DELETE FROM daily_price WHERE trade_date < :cutoff"),
                {"cutoff": cutoff},
            ).rowcount
            if deleted:
                logger.info(f"清理历史行情 {deleted} 条 (早于 {cutoff})")
