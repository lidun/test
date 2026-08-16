"""系统主控：初始化组件、配置调度器、启动 Web 服务"""
from __future__ import annotations

import argparse
import asyncio

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from src.core.config import config, setup_logging

setup_logging()


class AIQuantSystem:
    def __init__(self):
        self.scheduler = None
        self.components = {}

    def init(self, with_knowledge: bool = True):
        """初始化所有组件"""
        from src.web.context import AppContext

        ctx = AppContext.get()
        ctx.init(with_knowledge=with_knowledge)

        from src.scheduler.jobs import ScheduledJobs

        jobs = ScheduledJobs(ctx)
        self.components = {
            "ctx": ctx,
            "jobs": jobs,
            "arena": ctx.arena,
            "evolution": ctx.evolution,
            "metrics": ctx.metrics,
            "reporter": ctx.reporter,
        }
        logger.info("系统初始化完成")
        return self

    async def init_and_run(self):
        """初始化并全量运行（调度器 + Web）"""
        self.init()
        self.start_scheduler()
        await self.run_web_only()

    def start_scheduler(self):
        """配置定时任务"""
        self.scheduler = AsyncIOScheduler()
        jobs = self.components["jobs"]
        # 每日: 数据更新 15:30, 模拟交易 16:00, 告警 16:30, 报告 17:00
        self.scheduler.add_job(jobs.daily_data_update, "cron", day_of_week="mon-fri", hour=15, minute=30, id="daily_data")
        self.scheduler.add_job(jobs.daily_simulation, "cron", day_of_week="mon-fri", hour=16, minute=0, id="daily_sim")
        self.scheduler.add_job(jobs.daily_alerts_check, "cron", day_of_week="mon-fri", hour=16, minute=30, id="daily_alert")
        self.scheduler.add_job(jobs.daily_report, "cron", day_of_week="mon-fri", hour=17, minute=0, id="daily_report")
        # 每周: 进化 周六10:00, 周报 周六18:00
        self.scheduler.add_job(jobs.weekly_evolution, "cron", day_of_week="sat", hour=10, id="weekly_evolve")
        self.scheduler.add_job(jobs.weekly_report, "cron", day_of_week="sat", hour=18, id="weekly_report")
        # 维护: 健康检查每小时, 数据清理凌晨3点
        self.scheduler.add_job(jobs.system_health_check, "interval", hours=1, id="health_check")
        self.scheduler.add_job(jobs.cleanup_old_data, "cron", hour=3, id="cleanup")
        self.scheduler.start()
        logger.info("调度器已启动")

    async def run_web_only(self):
        """仅启动 Web 服务"""
        if not self.components:
            self.init()
        # 竞技场为空时先尝试从数据库恢复，仍为空再注入初始种群并回放
        try:
            if not self.components["arena"].strategies:
                self.components["evolution"]._ensure_arena_populated()
                if not self.components["arena"].strategies:
                    self.components["evolution"].seed_initial_population()
                await self.components["evolution"].run_simulation_replay(days=60)
        except Exception as e:
            logger.warning(f"Web 启动初始化数据失败: {e}")
        from src.web.app import app

        logger.info(f"Web 服务启动: http://{config.web.host}:{config.web.port}")
        server = uvicorn.Server(
            uvicorn.Config(
                app=app, host=config.web.host, port=config.web.port, log_level="info"
            )
        )
        await server.serve()

    async def backfill_data(self, years: int = 3, codes: list[str] | None = None):
        """回填历史数据"""
        self.init(with_knowledge=False)
        await self.components["ctx"].data_pipeline.backfill_history(years=years, codes=codes)

    async def manual_evolve(self):
        """手动触发一次进化"""
        self.init()
        summary = await self.components["evolution"].evolve()
        logger.info(f"手动进化完成: {summary}")
        return summary

    async def manual_simulate(self, days: int = 30):
        """手动回放最近 N 个交易日模拟交易"""
        from datetime import timedelta

        from src.core.database import get_db_session
        from sqlalchemy import text

        self.init()
        ctx = self.components["ctx"]
        with get_db_session() as session:
            rows = session.execute(
                text("SELECT DISTINCT trade_date FROM daily_price ORDER BY trade_date DESC LIMIT :n"),
                {"n": days},
            ).fetchall()
        trade_dates = sorted(r[0] for r in rows)
        for d in trade_dates:
            await ctx.arena.run_daily(d)
        logger.info(f"回放模拟完成，共 {len(trade_dates)} 个交易日")
        return len(trade_dates)


async def _async_main(args):
    system = AIQuantSystem()
    if args.run:
        await system.init_and_run()
    elif args.backfill is not None:
        await system.backfill_data(args.backfill)
    elif args.simulate:
        await system.manual_simulate(args.simulate)
    elif args.evolve:
        await system.manual_evolve()
    elif args.init:
        system.init()
    else:
        await system.run_web_only()


def main():
    parser = argparse.ArgumentParser(description="AI自主进化选股系统")
    parser.add_argument("--run", action="store_true", help="全量运行（调度器+Web）")
    parser.add_argument("--init", action="store_true", help="仅初始化数据库")
    parser.add_argument("--web", action="store_true", help="仅启动 Web")
    parser.add_argument("--backfill", type=int, metavar="YEARS", help="回填N年历史数据")
    parser.add_argument("--simulate", type=int, metavar="DAYS", help="回放最近N个交易日模拟交易")
    parser.add_argument("--evolve", action="store_true", help="手动触发一次进化")
    args = parser.parse_args()
    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
