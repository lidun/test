"""定时任务：每日数据更新、模拟交易、告警、报告、每周进化"""
from __future__ import annotations

from loguru import logger

from src.web.context import AppContext


class ScheduledJobs:
    def __init__(self, context: AppContext):
        self.ctx = context

    @property
    def data_pipeline(self):
        return self.ctx.data_pipeline

    @property
    def arena(self):
        return self.ctx.arena

    @property
    def metrics(self):
        return self.ctx.metrics

    @property
    def alert(self):
        return self.ctx.alert

    @property
    def evolution(self):
        return self.ctx.evolution

    @property
    def reporter(self):
        return self.ctx.reporter

    async def daily_data_update(self):
        """每日数据更新 (15:30)"""
        logger.info("[定时] 每日数据更新")
        await self.data_pipeline.daily_update()

    async def daily_simulation(self):
        """每日模拟交易 (16:00)"""
        logger.info("[定时] 每日模拟交易")
        await self.arena.run_daily()

    async def daily_alerts_check(self):
        """每日告警检查 (16:30)"""
        logger.info("[定时] 每日告警检查")
        alerts = self.metrics.check_strategy_alerts()
        system_metrics = self.metrics.collect_system_metrics()
        self.alert.check_and_alert(alerts, system_metrics)

    async def daily_report(self):
        """每日报告生成 (17:00)"""
        logger.info("[定时] 每日报告生成")
        self.reporter.generate_full_report("daily")

    async def weekly_evolution(self):
        """每周进化 (周六 10:00)"""
        logger.info("[定时] 每周进化")
        await self.evolution.evolve()

    async def weekly_report(self):
        """每周报告 (周六 18:00)"""
        logger.info("[定时] 每周报告")
        self.reporter.generate_full_report("weekly")

    async def system_health_check(self):
        """系统健康检查 (每小时)"""
        health = self.metrics.health_check()
        if health["status"] != "healthy":
            self.alert.send_system_alert(f"系统状态: {health['status']}", "warning")

    async def cleanup_old_data(self):
        """数据清理 (凌晨3点)：保留最近2年行情，清理过期缓存"""
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
