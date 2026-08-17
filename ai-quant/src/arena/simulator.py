"""模拟引擎：管理多个活跃策略的竞技场"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Dict, Optional

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import text

from src.core.cache import redis_cache
from src.core.config import config
from src.core.database import get_db_session
from src.strategy.schema import StrategyStatus


class SimulationEngine:
    def __init__(self, max_strategies=None):
        self.max_strategies = max_strategies or config.strategy.max_active_strategies
        self.initial_capital = config.strategy.initial_capital
        self.strategies: Dict[str, "LiveStrategy"] = {}
        self.benchmark_nav = []
        self.last_update_date = None

    # ---------- 策略管理 ----------
    def add_strategy(self, strategy_id, strategy_func, strategy_meta):
        if len(self.strategies) >= self.max_strategies:
            logger.warning(f"竞技场已满 ({self.max_strategies})，拒绝添加 {strategy_id}")
            return None
        from src.arena.live_strategy import LiveStrategy

        live = LiveStrategy(strategy_id, strategy_func, strategy_meta, self.initial_capital)
        self.strategies[strategy_id] = live
        return live

    def remove_strategy(self, strategy_id):
        if strategy_id in self.strategies:
            self.strategies[strategy_id].status = StrategyStatus.ELIMINATED
            del self.strategies[strategy_id]
            return True
        return False

    def get_strategy(self, strategy_id):
        return self.strategies.get(strategy_id)

    def has_strategy(self, strategy_id) -> bool:
        return strategy_id in self.strategies

    def strategy_ids(self) -> list[str]:
        return list(self.strategies.keys())

    def _ensure_loaded(self):
        """从数据库恢复 active 策略，保证新进程/重启后竞技场非空"""
        if self.strategies:
            return
        with get_db_session() as session:
            rows = session.execute(
                text(
                    "SELECT id, meta FROM strategies WHERE status = 'active' ORDER BY created_at"
                )
            ).fetchall()
        loaded = 0
        for sid, meta in rows:
            if self.has_strategy(sid):
                continue
            try:
                meta_dict = meta if isinstance(meta, dict) else json.loads(meta)
            except (TypeError, ValueError):
                continue
            from src.strategy.compiler import StrategyCompiler

            success, _, func, _obj = StrategyCompiler().compile(meta_dict)
            if success:
                self.add_strategy(sid, func, meta_dict)
                loaded += 1
        if loaded:
            logger.info(f"竞技场从数据库恢复 {loaded} 个活跃策略")

    # ---------- 每日运行 ----------
    async def run_daily(self, trade_date: Optional[str] = None):
        if trade_date is None:
            trade_date = date.today()
        elif isinstance(trade_date, str):
            trade_date = pd.to_datetime(trade_date).date()

        self._ensure_loaded()

        # 因子数据缺失时按需计算（首次运行自动触发，批量回填30天）
        if self._need_factor_compute():
            try:
                from src.factor.engine import FactorEngine

                await FactorEngine().calculate_history(days=30)
            except Exception as e:
                logger.warning(f"按需因子计算失败: {e}")

        factor_data = self._get_factor_data(trade_date)
        prices = self._get_prices(trade_date)
        if not prices:
            logger.warning(f"{trade_date} 无价格数据，跳过模拟")
            return

        for sid, strategy in self.strategies.items():
            try:
                strategy.update_daily(factor_data, trade_date, prices)
                self._persist_performance(sid, strategy, trade_date)
            except Exception as e:
                logger.error(f"策略 {sid} 每日更新失败: {e}")
        self.last_update_date = trade_date
        logger.info(
            f"模拟交易完成: {trade_date}, 活跃策略 {len(self.strategies)} 个"
        )

    def _need_factor_compute(self) -> bool:
        try:
            with get_db_session() as session:
                count = session.execute(text("SELECT COUNT(*) FROM factor_data")).scalar()
            return bool(not count)
        except Exception:
            return False

    def _get_factor_data(self, trade_date) -> pd.DataFrame:
        """加载因子快照横截面（含基础列），优先走缓存"""
        date_str = trade_date.strftime("%Y%m%d")
        cache_key = f"factor_snapshot:{date_str}"
        cached = redis_cache.get(cache_key)
        if cached:
            try:
                data = json.loads(cached)
                df = pd.DataFrame.from_dict(data, orient="index")
                return df
            except (ValueError, TypeError):
                pass
        with get_db_session() as session:
            result = session.execute(
                text(
                    """
                    SELECT d.ts_code, d.close, d.turnover_rate,
                           f.factor_name, f.factor_value
                    FROM daily_price d
                    LEFT JOIN factor_data f
                      ON f.ts_code = d.ts_code AND f.trade_date = d.trade_date
                    WHERE d.trade_date = :date
                    """
                ),
                {"date": trade_date},
            )
            rows = result.fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(
            rows, columns=["ts_code", "close", "turnover_rate", "factor_name", "factor_value"]
        )
        pivot = df.pivot_table(
            index="ts_code", columns="factor_name", values="factor_value", aggfunc="first"
        )
        pivot["close"] = df.groupby("ts_code")["close"].first()
        pivot["turnover_rate"] = df.groupby("ts_code")["turnover_rate"].first()
        return pivot

    def _get_prices(self, trade_date) -> Dict[str, float]:
        with get_db_session() as session:
            result = session.execute(
                text("SELECT ts_code, close FROM daily_price WHERE trade_date = :date"),
                {"date": trade_date},
            )
            rows = result.fetchall()
        return {code: float(close) for code, close in rows if close is not None}

    def _persist_performance(self, sid, strategy, trade_date):
        stats = strategy.get_stats()
        with get_db_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO strategy_performance
                        (strategy_id, trade_date, nav, daily_return, cumulative_return,
                         positions_count, cash, total_value)
                    VALUES (:sid, :date, :nav, :daily_return, :cumulative_return,
                            :positions, :cash, :total_value)
                    ON CONFLICT (strategy_id, trade_date) DO UPDATE SET
                        nav = EXCLUDED.nav,
                        total_value = EXCLUDED.total_value,
                        positions_count = EXCLUDED.positions_count
                    """
                ),
                {
                    "sid": sid,
                    "date": trade_date,
                    "nav": stats.get("total_value", strategy.total_value),
                    "daily_return": strategy.nav_history[-1]["daily_return"]
                    if strategy.nav_history
                    else 0,
                    "cumulative_return": stats.get("total_return", 0),
                    "positions": stats.get("positions", 0),
                    "cash": stats.get("cash", strategy.cash),
                    "total_value": strategy.total_value,
                },
            )

    # ---------- 统计 ----------
    def get_leaderboard(self, metric: str = "sharpe") -> pd.DataFrame:
        records = []
        for sid, s in self.strategies.items():
            r = s.get_stats()
            r["strategy_id"] = sid
            r["type"] = s.meta.get("type", "hybrid")
            records.append(r)
        if not records:
            return pd.DataFrame(
                columns=[
                    "strategy_id", "name", "total_return", "sharpe", "max_drawdown",
                    "win_rate", "total_value", "positions",
                ]
            )
        df = pd.DataFrame(records)
        if metric in df.columns:
            df = df.sort_values(metric, ascending=False).reset_index(drop=True)
        return df

    def get_arena_stats(self) -> dict:
        stats = self.get_leaderboard()
        if len(stats) == 0:
            return {
                "total_strategies": 0,
                "total_capital": 0,
                "avg_return": 0,
                "positive_count": 0,
                "negative_count": 0,
            }
        returns = stats["total_return"].tolist()
        return {
            "total_strategies": len(stats),
            "total_capital": round(stats["total_value"].sum(), 2),
            "avg_return": round(float(np.mean(returns)), 4),
            "positive_count": int(sum(1 for r in returns if r > 0)),
            "negative_count": int(sum(1 for r in returns if r <= 0)),
            "best_strategy": stats.iloc[0]["name"] if len(stats) else "",
            "best_return": round(float(stats.iloc[0]["total_return"]), 4) if len(stats) else 0,
        }

    def get_benchmark(self) -> pd.DataFrame:
        """基准：沪深300 或全市场等权（简化用上证指数代理）"""
        if self.benchmark_nav:
            return pd.DataFrame(self.benchmark_nav)
        with get_db_session() as session:
            result = session.execute(
                text(
                    """
                    SELECT trade_date, close FROM daily_price
                    WHERE ts_code = :code
                    ORDER BY trade_date
                    """
                ),
                {"code": "000001.SH"},
            )
            rows = result.fetchall()
        if not rows:
            return pd.DataFrame(columns=["date", "nav"])
        closes = [float(r[1]) for r in rows]
        base = closes[0]
        records = [
            {"date": r[0], "nav": c / base} for r, c in zip(rows, closes)
        ]
        self.benchmark_nav = records
        return pd.DataFrame(records)
