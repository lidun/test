"""系统主控：初始化组件、配置调度器、启动 Web 服务"""
from __future__ import annotations

import argparse
import asyncio

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from src.core.config import config, setup_logging

setup_logging()


class AIQuantSystem:
    def __init__(self):
        self.scheduler = None
        self.components = {}

    def init(self):
        """初始化所有组件"""
        from src.web.context import AppContext

        ctx = AppContext.get()
        ctx.init()

        from src.scheduler.jobs import ScheduledJobs

        jobs = ScheduledJobs(ctx)
        self.components = {
            "ctx": ctx,
            "jobs": jobs,
            "store": ctx.store,
            "assistant": ctx.assistant,
            "task_scheduler": ctx.task_scheduler,
            "metrics": ctx.metrics,
            "alert": ctx.alert,
        }
        logger.info("系统初始化完成")
        return self

    async def init_and_run(self):
        """初始化并全量运行（调度器 + Web）"""
        self.init()
        self.start_scheduler()
        await self.run_web_only()

    def start_scheduler(self):
        """配置定时任务：系统任务 + 各 Agent 定时任务"""
        self.scheduler = AsyncIOScheduler()
        jobs = self.components["jobs"]
        cfg = {}
        try:
            from src.core.config_store import ConfigStore

            cfg = ConfigStore().get_all()
        except Exception:
            cfg = {}

        def hm(key: str, default: str) -> tuple[int, int]:
            raw = cfg.get(key, default)
            try:
                h, m = raw.split(":")
                return int(h), int(m)
            except (ValueError, AttributeError):
                d = default.split(":")
                return int(d[0]), int(d[1])

        try:
            health_interval = max(1, int(float(cfg.get("system.health_check_interval", "1"))))
        except ValueError:
            health_interval = 1

        data_h, data_m = hm("system.daily_data_time", "15:30")
        sim_h, sim_m = hm("system.daily_sim_time", "16:00")
        alert_h, alert_m = hm("system.daily_alert_time", "16:30")

        # 每日: 数据更新, Agent 估值, 告警
        self.scheduler.add_job(jobs.daily_data_update, "cron", day_of_week="mon-fri", hour=data_h, minute=data_m, id="daily_data")
        self.scheduler.add_job(jobs.daily_agent_mark, "cron", day_of_week="mon-fri", hour=sim_h, minute=sim_m, id="daily_mark")
        self.scheduler.add_job(jobs.daily_alerts_check, "cron", day_of_week="mon-fri", hour=alert_h, minute=alert_m, id="daily_alert")
        # 维护: 健康检查每小时, 数据清理凌晨3点
        self.scheduler.add_job(jobs.system_health_check, "interval", hours=health_interval, id="health_check")
        self.scheduler.add_job(jobs.cleanup_old_data, "cron", hour=3, id="cleanup")

        # Agent 定时任务（从数据库注册）
        self.components["task_scheduler"].scheduler = self.scheduler
        self.components["task_scheduler"].sync_all()
        self.scheduler.start()
        logger.info(
            f"调度器已启动 (数据{data_h:02d}:{data_m:02d} 估值{sim_h:02d}:{sim_m:02d} "
            f"告警{alert_h:02d}:{alert_m:02d} Agent任务{len(self.components['task_scheduler'].job_map)}个)"
        )

    async def run_web_only(self):
        """仅启动 Web 服务"""
        if not self.components:
            self.init()
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
        self.init()
        await self.components["ctx"].data_pipeline.backfill_history(years=years, codes=codes)


async def _async_main(args):
    system = AIQuantSystem()
    if args.run:
        await system.init_and_run()
    elif args.backfill is not None:
        await system.backfill_data(args.backfill)
    elif args.init:
        system.init()
    else:
        await system.run_web_only()


def main():
    parser = argparse.ArgumentParser(description="AI 交易 Agent 系统")
    parser.add_argument("--run", action="store_true", help="全量运行（调度器+Web）")
    parser.add_argument("--init", action="store_true", help="仅初始化数据库")
    parser.add_argument("--web", action="store_true", help="仅启动 Web")
    parser.add_argument("--backfill", type=int, metavar="YEARS", help="回填N年历史数据")
    args = parser.parse_args()
    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
