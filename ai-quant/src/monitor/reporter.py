"""报告生成器：汇总竞技场、行情与系统状态，生成日报/周报/月报并入库"""
from __future__ import annotations

from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import text

from src.core.database import get_db_session

_REPORT_TITLES = {
    "daily": "日报",
    "weekly": "周报",
    "monthly": "月报",
}


class ReportGenerator:
    def generate_full_report(self, report_type: str = "daily", save: bool = True) -> str:
        if report_type == "monthly":
            lines = self._build_monthly_report()
        elif report_type == "weekly":
            lines = self._build_weekly_report()
        else:
            lines = self._build_daily_report()
        report = "\n".join(lines)
        if save:
            self._save(report_type, report)
        logger.info(f"已生成{_REPORT_TITLES.get(report_type, report_type)}报告 ({len(lines)} 行)")
        return report

    def _save(self, report_type: str, content: str) -> None:
        try:
            with get_db_session() as session:
                session.execute(
                    text(
                        """
                        INSERT INTO report_history (report_type, title, content)
                        VALUES (:t, :title, :content)
                        """
                    ),
                    {
                        "t": report_type,
                        "title": f"{_REPORT_TITLES.get(report_type, report_type)} "
                        f"{datetime.now():%Y-%m-%d %H:%M}",
                        "content": content,
                    },
                )
        except Exception as e:
            logger.warning(f"报告入库失败: {e}")

    def _header(self, title: str) -> list[str]:
        now = datetime.now()
        return [
            "=" * 56,
            f"AI自主进化选股系统 - {title} {now:%Y-%m-%d %H:%M}",
            "=" * 56,
        ]

    def _arena_summary(self) -> list[str]:
        with get_db_session() as session:
            row = session.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT strategy_id),
                           SUM(total_value),
                           AVG(cumulative_return),
                           COUNT(*) FILTER (WHERE cumulative_return >= 0)
                    FROM (
                        SELECT strategy_id,
                               MAX(total_value) AS total_value,
                               MAX(cumulative_return) AS cumulative_return
                        FROM strategy_performance
                        GROUP BY strategy_id
                    ) t
                    """
                )
            ).fetchone()
        if not row or not row[0]:
            return ["\n## 竞技场概况", "  暂无策略表现数据"]
        n, capital, avg, positive = row
        lines = ["\n## 竞技场概况"]
        lines.append(f"  策略数: {n} | 总资产: {float(capital):,.0f} | "
                     f"平均收益: {float(avg)*100:+.2f}% | 盈利: {positive}/{n}")
        return lines

    def _top_strategies(self, limit: int = 5) -> list[str]:
        with get_db_session() as session:
            rows = session.execute(
                text(
                    """
                    SELECT strategy_id, MAX(nav) AS nav,
                           MAX(cumulative_return) AS cum_return,
                           MAX(positions_count) AS positions
                    FROM strategy_performance
                    GROUP BY strategy_id
                    ORDER BY cum_return DESC NULLS LAST
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).fetchall()
        lines = [f"\n## 策略排行 TOP{len(rows)} (按累计收益)"]
        for sid, nav, cum, pos in rows:
            lines.append(
                f"  {sid}: 净值 {nav:.2f}, 累计收益 {cum:+.2%}, 持仓 {pos or 0} 只"
            )
        return lines

    def _market_overview(self) -> list[str]:
        with get_db_session() as session:
            row = session.execute(
                text(
                    """
                    SELECT trade_date, COUNT(DISTINCT ts_code),
                           AVG(change_pct),
                           SUM(amount)
                    FROM daily_price
                    WHERE trade_date = (SELECT MAX(trade_date) FROM daily_price)
                    GROUP BY trade_date
                    """
                )
            ).fetchone()
        if not row or not row[0]:
            return ["\n## 大盘概况", "  暂无行情数据"]
        d, n, avg_chg, amount = row
        lines = [f"\n## 大盘概况 ({d})"]
        lines.append(
            f"  覆盖 {n} 只股票 | 平均涨跌 {float(avg_chg or 0):+.2f}% | "
            f"成交额 {float(amount or 0)/1e8:,.0f} 亿"
        )
        return lines

    def _evolution_latest(self) -> list[str]:
        with get_db_session() as session:
            row = session.execute(
                text(
                    "SELECT cycle, timestamp, added_count, arena_size "
                    "FROM evolution_log ORDER BY cycle DESC LIMIT 1"
                )
            ).fetchone()
        if row:
            return [
                f"\n## 最近进化周期 #{row[0]} "
                f"({row[1].strftime('%m-%d %H:%M') if row[1] else '-'}): "
                f"上线{row[2]}个, 竞技场共{row[3]}个"
            ]
        return ["\n## 最近进化周期", "  尚未执行进化周期"]

    def _period_performance(self, days: int, title: str) -> list[str]:
        with get_db_session() as session:
            rows = session.execute(
                text(
                    """
                    SELECT strategy_id,
                           MIN(nav) AS start_nav,
                           MAX(nav) AS end_nav,
                           MIN(cumulative_return) AS min_ret,
                           MAX(cumulative_return) AS max_ret
                    FROM strategy_performance
                    WHERE trade_date >= CURRENT_DATE - :days * INTERVAL '1 day'
                    GROUP BY strategy_id
                    ORDER BY max_ret DESC NULLS LAST
                    LIMIT 8
                    """
                ),
                {"days": days},
            ).fetchall()
        if not rows:
            return [f"\n## {title}表现", "  该周期内暂无数据"]
        lines = [f"\n## {title}表现 (近{days}天 TOP8)"]
        for sid, start_nav, end_nav, min_ret, max_ret in rows:
            if start_nav and end_nav and start_nav > 0:
                chg = (end_nav - start_nav) / start_nav
                lines.append(f"  {sid}: 区间收益 {chg:+.2%} (区间最大回撤后最低 {float(min_ret or 0)*100:+.1f}%)")
            else:
                lines.append(f"  {sid}: 期末净值 {end_nav:.2f}")
        return lines

    def _build_daily_report(self) -> list[str]:
        lines = self._header("日报")
        lines += self._arena_summary()
        lines += self._top_strategies(5)
        lines += self._market_overview()
        lines += self._evolution_latest()
        lines.append("\n" + "-" * 56)
        lines.append("风险提示: 模拟盘仅供学习研究，不构成投资建议。")
        return lines

    def _build_weekly_report(self) -> list[str]:
        lines = self._header("周报")
        lines += self._arena_summary()
        lines += self._period_performance(7, "本周")
        lines += self._top_strategies(5)
        lines += self._market_overview()
        lines += self._evolution_latest()
        lines.append("\n" + "-" * 56)
        lines.append("周度总结: 请结合净值走势与策略详情评估下周转仓计划。")
        return lines

    def _build_monthly_report(self) -> list[str]:
        lines = self._header("月报")
        lines += self._arena_summary()
        lines += self._period_performance(30, "本月")
        lines += self._top_strategies(8)
        lines += self._market_overview()
        lines += self._evolution_latest()
        lines.append("\n" + "-" * 56)
        lines.append("月度总结: 建议淘汰长期回撤过大策略，保留稳定盈利精英并触发下一轮进化。")
        return lines

    def history(self, report_type: str | None = None, limit: int = 20) -> list[dict]:
        sql = """
            SELECT id, report_type, title, content, created_at
            FROM report_history
        """
        params: dict = {}
        if report_type:
            sql += " WHERE report_type = :t"
            params["t"] = report_type
        sql += " ORDER BY created_at DESC LIMIT :limit"
        params["limit"] = limit
        with get_db_session() as session:
            rows = session.execute(text(sql), params).fetchall()
        return [
            {
                "id": r[0],
                "report_type": r[1],
                "title": r[2],
                "content": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
            }
            for r in rows
        ]
