"""监控指标采集与健康检查"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import psutil
from loguru import logger
from sqlalchemy import text

from src.core.config import config
from src.core.database import get_db_session


@dataclass
class SystemMetrics:
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_used_gb: float = 0.0
    disk_percent: float = 0.0
    active_strategies: int = 0
    db_ok: bool = True

    def to_dict(self) -> dict:
        return {
            "cpu_percent": round(self.cpu_percent, 1),
            "memory_percent": round(self.memory_percent, 1),
            "memory_used_gb": round(self.memory_used_gb, 2),
            "disk_percent": round(self.disk_percent, 1),
            "active_strategies": self.active_strategies,
            "db_ok": self.db_ok,
        }


@dataclass
class StrategyAlert:
    strategy_id: str
    strategy_name: str
    alert_type: str
    severity: str = "warning"
    message: str = ""
    current_value: float = 0.0
    threshold: float = 0.0


class MetricsCollector:
    def __init__(self, arena=None):
        self.arena = arena

    def collect_system_metrics(self) -> SystemMetrics:
        metrics = SystemMetrics()
        try:
            metrics.cpu_percent = psutil.cpu_percent(interval=0.5)
            memory = psutil.virtual_memory()
            metrics.memory_percent = memory.percent
            metrics.memory_used_gb = memory.used / (1024**3)
            disk = psutil.disk_usage("/")
            metrics.disk_percent = disk.percent
        except Exception as e:
            logger.warning(f"采集系统指标失败: {e}")
        if self.arena:
            metrics.active_strategies = len(self.arena.strategies)
        metrics.db_ok = self.health_check()["status"] == "healthy"
        return metrics

    def check_strategy_alerts(self) -> List[StrategyAlert]:
        alerts = []
        if not self.arena:
            return alerts
        for sid, strategy in self.arena.strategies.items():
            stats = strategy.get_stats()
            max_dd = stats.get("max_drawdown", 0)
            threshold = config.alert.max_drawdown_alert
            if max_dd < threshold:
                severity = "critical" if max_dd < threshold * 1.5 else "warning"
                alerts.append(
                    StrategyAlert(
                        strategy_id=sid,
                        strategy_name=strategy.name,
                        alert_type="drawdown",
                        severity=severity,
                        message=f"最大回撤 {max_dd:.1%}",
                        current_value=round(float(max_dd), 4),
                        threshold=threshold,
                    )
                )
            total_return = stats.get("total_return", 0)
            if total_return < -0.30:
                alerts.append(
                    StrategyAlert(
                        strategy_id=sid,
                        strategy_name=strategy.name,
                        alert_type="deep_loss",
                        severity="critical",
                        message=f"累计亏损 {total_return:.1%}",
                        current_value=round(float(total_return), 4),
                        threshold=-0.30,
                    )
                )
        return alerts

    def health_check(self) -> dict:
        checks = {"status": "healthy", "checks": {}}
        try:
            with get_db_session() as session:
                session.execute(text("SELECT 1"))
            checks["checks"]["database"] = {"status": "ok"}
        except Exception as e:
            checks["checks"]["database"] = {"status": "error", "message": str(e)}
            checks["status"] = "unhealthy"
        try:
            from src.core.cache import redis_cache

            redis_ok = redis_cache.ping()
            checks["checks"]["redis"] = {"status": "ok" if redis_ok else "degraded"}
        except Exception as e:
            checks["checks"]["redis"] = {"status": "error", "message": str(e)}
        return checks
