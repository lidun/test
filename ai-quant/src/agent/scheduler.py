"""Agent 定时自动任务调度：每个 Agent 可配置独立的定时任务（每日/间隔）"""
from __future__ import annotations

import asyncio

from loguru import logger

from src.agent.assistant import AgentAssistant
from src.agent.store import AgentStore


class AgentTaskScheduler:
    """管理所有 Agent 定时任务的注册/注销（基于 APScheduler）"""

    def __init__(self, assistant: AgentAssistant, store: AgentStore, scheduler):
        self.assistant = assistant
        self.store = store
        self.scheduler = scheduler
        self.job_map: dict[int, str] = {}  # task_id -> apscheduler job_id

    def sync_all(self):
        """全量同步：以数据库为准重新注册所有启用任务"""
        self._remove_all()
        for agent in self.store.list_agents():
            if agent.status != "running":
                continue
            for task in self.store.list_tasks(agent.id):
                if task.enabled:
                    self.register(task)

    def register(self, task):
        if task.id in self.job_map:
            return
        if self.scheduler is None:
            logger.debug(f"调度器未启动，跳过定时任务 #{task.id} 注册（Web-only 模式）")
            return
        job_id = f"agent_task_{task.id}"
        try:
            if task.schedule_type == "interval":
                hours = max(0.5, task.interval_hours or 4)
                self.scheduler.add_job(
                    self._run, "interval", hours=hours,
                    id=job_id, args=[task.id], replace_existing=True,
                )
            else:  # daily
                h, m = self._parse_hm(task.schedule_time or "09:30")
                self.scheduler.add_job(
                    self._run, "cron", day_of_week="mon-fri",
                    hour=h, minute=m, id=job_id, args=[task.id],
                    replace_existing=True,
                )
            self.job_map[task.id] = job_id
            logger.info(f"已注册定时任务 #{task.id} (agent={task.agent_id}, "
                        f"{task.schedule_type} {task.schedule_time or task.interval_hours})")
        except Exception as e:
            logger.error(f"注册定时任务 #{task.id} 失败: {e}")

    def unregister(self, task_id: int):
        job_id = self.job_map.pop(task_id, None)
        if job_id and self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
            logger.info(f"已注销定时任务 #{task_id}")

    def _remove_all(self):
        for task_id in list(self.job_map):
            self.unregister(task_id)

    async def _run(self, task_id: int):
        task = self.store.get_task(task_id)
        if not task or not task.enabled:
            return
        agent = self.store.get_agent(task.agent_id)
        if not agent or agent.status != "running":
            return
        logger.info(f"触发 Agent 定时任务 #{task_id}: {agent.name}")
        try:
            text = await asyncio.to_thread(self.assistant.auto_run, agent)
            self.store.mark_task_run(task_id)
            logger.info(f"Agent {agent.name} 定时任务完成: {(text or '')[:120]}")
        except Exception as e:
            logger.error(f"Agent {agent.name} 定时任务执行失败: {e}")

    @staticmethod
    def _parse_hm(value: str) -> tuple[int, int]:
        try:
            h, m = value.split(":")
            return int(h), int(m)
        except (ValueError, AttributeError):
            return 9, 30
