"""报告生成器：汇总竞技场与系统状态，生成文本报告"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
from loguru import logger
from sqlalchemy import text

from src.core.database import get_db_session


class ReportGenerator:
    def generate_full_report(self, report_type: str = "daily") -> str:
        if report_type == "daily":
            lines = self._build_daily_report()
        else:
            lines = self._build_weekly_report()
        report = "\n".join(lines)
        logger.info(f"已生成{report_type}报告 ({len(lines)} 行)")
        return report

    def _build_daily_report(self) -> list[str]:
        lines = [
            "=" * 50,
            f"AI自主进化选股系统 - 日报 {datetime.now():%Y-%m-%d %H:%M}",
            "=" * 50,
        ]
        with get_db_session() as session:
            arena = session.execute(
                text(
                    """
                    SELECT strategy_id, MAX(nav) as nav,
                           MAX(cumulative_return) as cum_return
                    FROM strategy_performance
                    GROUP BY strategy_id ORDER BY cum_return DESC NULLS LAST
                    LIMIT 10
                    """
                )
            ).fetchall()
            evolution = session.execute(
                text(
                    "SELECT cycle, added_count, arena_size FROM evolution_log "
                    "ORDER BY cycle DESC LIMIT 1"
                )
            ).fetchone()
        lines.append(f"\n## 竞技场（最近10个活跃策略）")
        for sid, nav, cum in arena:
            lines.append(
                f"  {sid}: 净值 {nav:.2f}, 累计收益 {cum:+.2%}" if cum is not None else f"  {sid}: 净值 {nav:.2f}"
            )
        if evolution:
            lines.append(
                f"\n## 最近进化周期 #{evolution[0]}: 上线{evolution[1]}个, 竞技场共{evolution[2]}个"
            )
        else:
            lines.append("\n## 尚未执行进化周期")
        return lines

    def _build_weekly_report(self) -> list[str]:
        lines = self._build_daily_report()
        lines.append("\n" + "=" * 50)
        lines.append("周度总结：请查看 Web 面板获取详细曲线与策略分析。")
        return lines
