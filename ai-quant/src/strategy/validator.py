"""轻量验证层：对策略进行四关快速验证

第1关 逻辑一致性
第2关 因子有效性
第3关 历史数据快速扫描（基于数据库回测）
第4关 统计显著性检验
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from loguru import logger
from scipy import stats
from sqlalchemy import text

from src.core.config import config
from src.core.database import get_db_session


@dataclass
class ValidationReport:
    strategy_name: str = ""
    passed: bool = False
    warnings: List[str] = field(default_factory=list)
    metrics: Dict = field(default_factory=dict)
    validation_time: float = 0.0

    def to_dict(self) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "passed": self.passed,
            "warnings": self.warnings,
            "metrics": self.metrics,
            "validation_time": round(self.validation_time, 3),
        }


class QuickValidator:
    def __init__(self):
        self.min_sharpe = config.strategy.min_sharpe_for_pass
        self.min_win_rate = config.strategy.min_win_rate
        self.history_years = config.strategy.history_validation_years
        self.max_conditions = config.strategy.max_conditions

    def validate(self, strategy_func, strategy_dict: dict, verbose: bool = True) -> ValidationReport:
        start_time = time.time()
        report = ValidationReport(strategy_name=strategy_dict.get("name", "unknown"))

        # 第1关：逻辑一致性检查
        logic_result = self._check_logic_consistency(strategy_dict)
        if not logic_result["passed"]:
            report.warnings.append(f"逻辑问题: {logic_result['issue']}")
            report.validation_time = time.time() - start_time
            return report

        # 第2关：因子有效性检查
        factor_result = self._check_factor_validity(strategy_dict)
        if not factor_result["passed"]:
            report.warnings.append(f"因子问题: {factor_result['issue']}")
            report.validation_time = time.time() - start_time
            return report

        # 第3关：历史数据快速扫描
        history_result = self._quick_history_scan(strategy_func, strategy_dict)
        report.metrics = history_result.get("metrics", {})
        if not history_result["passed"]:
            report.warnings.append(f"历史验证: {history_result['reason']}")
            report.validation_time = time.time() - start_time
            return report

        # 第4关：统计显著性检验
        returns = history_result.get("returns", np.array([]))
        stat_result = self._statistical_tests(returns)
        if not stat_result["passed"]:
            report.warnings.append(f"统计问题: {stat_result['reason']}")
            report.validation_time = time.time() - start_time
            return report

        report.passed = True
        report.validation_time = time.time() - start_time
        if verbose:
            logger.info(f"策略 [{report.strategy_name}] 验证通过: {report.metrics}")
        return report

    def _check_logic_consistency(self, strategy_dict: dict) -> dict:
        conditions = strategy_dict.get("conditions", [])
        issues = []
        factors = [c.get("factor") for c in conditions if isinstance(c, dict)]
        if len(factors) != len(set(factors)):
            issues.append("存在重复因子条件")
        if len(conditions) > self.max_conditions:
            issues.append(f"条件过多({len(conditions)}>{self.max_conditions})")
        stop_loss = strategy_dict.get("stop_loss", -0.08)
        stop_profit = strategy_dict.get("stop_profit", 0.30)
        try:
            if stop_loss >= 0:
                issues.append("止损线应为负数")
            if stop_profit <= 0:
                issues.append("止盈线应为正数")
            if abs(stop_loss) > stop_profit:
                issues.append(f"盈亏比不合理: 止损{stop_loss:.0%} > 止盈{stop_profit:.0%}")
        except (TypeError, ValueError):
            issues.append("止损/止盈格式错误")
        return {"passed": len(issues) == 0, "issue": "; ".join(issues) if issues else ""}

    def _check_factor_validity(self, strategy_dict: dict) -> dict:
        known = {
            "ma_20", "ma_60", "rsi_14", "macd_dif", "macd_hist", "boll_width",
            "close_ma_20", "close_ma_60", "ret_5d", "ret_20d", "ret_60d",
            "volatility_20d", "max_drawdown_60d", "volume_ratio", "volume_ratio_5d",
            "turnover_rate", "pe_ttm", "pb", "roe", "dividend_yield",
            "revenue_growth", "profit_growth", "net_inflow_large", "north_inflow",
        }
        unknown = set()
        for c in strategy_dict.get("conditions", []):
            if isinstance(c, dict) and c.get("factor") not in known:
                unknown.add(c.get("factor"))
        for r in strategy_dict.get("ranking_factors", []):
            if isinstance(r, dict) and r.get("factor") not in known:
                unknown.add(r.get("factor"))
        if unknown:
            return {"passed": False, "issue": f"未知因子: {', '.join(sorted(unknown))}"}
        return {"passed": True, "issue": ""}

    def _quick_history_scan(self, strategy_func, strategy_dict: dict) -> dict:
        """基于数据库历史数据做快速回测。

        回测思路（简化）：
        1. 取最近 N 个交易日（默认 90 天）
        2. 每个交易日构建当日因子横截面（从 factor_data + daily_price 组装）
        3. 用 strategy_func 选股，取次日收益均值作为当日策略收益
        4. 汇总为收益序列，计算指标
        """
        lookback_days = 90
        trade_dates = self._get_trade_dates(lookback_days)
        if len(trade_dates) < 20:
            reason = "数据库历史数据不足，无法回测"
            logger.warning(f"[{strategy_dict.get('name')}] {reason}")
            return {"passed": False, "reason": reason, "metrics": {}}

        returns = []
        for i in range(len(trade_dates) - 1):
            today = trade_dates[i]
            nxt = trade_dates[i + 1]
            snapshot = self._load_snapshot(today)
            if snapshot is None or snapshot.empty:
                continue
            picks = strategy_func(snapshot)
            if picks is None or len(picks) == 0:
                continue
            codes = picks.index.tolist()
            next_returns = self._get_next_returns(codes, today, nxt)
            if len(next_returns) > 0:
                returns.append(np.mean(next_returns))

        if len(returns) < 8:
            return {
                "passed": False,
                "reason": f"有效交易天数过少: {len(returns)}",
                "metrics": {},
            }

        returns_arr = np.array(returns)
        total_return = float(np.prod(1 + returns_arr) - 1)
        sharpe = float(returns_arr.mean() / returns_arr.std() * np.sqrt(252)) if returns_arr.std() > 0 else 0
        win_rate = float((returns_arr > 0).mean())
        cum = np.cumprod(1 + returns_arr)
        peak = np.maximum.accumulate(cum)
        max_dd = float(((cum - peak) / peak).min())

        metrics = {
            "total_return": round(total_return, 4),
            "sharpe": round(sharpe, 4),
            "max_drawdown": round(max_dd, 4),
            "win_rate": round(win_rate, 4),
            "trading_days": len(returns_arr),
            "avg_daily_return": round(float(returns_arr.mean()), 6),
        }

        if sharpe < self.min_sharpe:
            return {
                "passed": False,
                "reason": f"夏普比率过低: {sharpe:.2f} < {self.min_sharpe}",
                "metrics": metrics,
                "returns": returns_arr,
            }
        if win_rate < self.min_win_rate:
            return {
                "passed": False,
                "reason": f"胜率过低: {win_rate:.0%} < {self.min_win_rate:.0%}",
                "metrics": metrics,
                "returns": returns_arr,
            }
        return {"passed": True, "metrics": metrics, "returns": returns_arr, "reason": ""}

    def _get_trade_dates(self, lookback_days: int) -> List[date]:
        with get_db_session() as session:
            result = session.execute(
                text(
                    """
                    SELECT DISTINCT trade_date FROM daily_price
                    WHERE trade_date >= :start
                    ORDER BY trade_date DESC LIMIT :n
                    """
                ),
                {
                    "start": date.today() - timedelta(days=lookback_days * 2),
                    "n": lookback_days,
                },
            )
            dates = [row[0] for row in result.fetchall()]
        dates.sort()
        return dates

    def _load_snapshot(self, trade_date: date) -> Optional[pd.DataFrame]:
        """加载某交易日因子横截面（宽表），索引为 ts_code"""
        with get_db_session() as session:
            result = session.execute(
                text(
                    """
                    SELECT d.ts_code,
                           d.close, d.turnover_rate,
                           f.factor_name, f.factor_value
                    FROM daily_price d
                    LEFT JOIN factor_data f
                      ON f.ts_code = d.ts_code AND f.trade_date = d.trade_date
                    WHERE d.trade_date = :date
                      AND d.ts_code IN (
                          SELECT ts_code FROM daily_price
                          WHERE trade_date = :date ORDER BY amount DESC LIMIT 500
                      )
                    """
                ),
                {"date": trade_date},
            )
            rows = result.fetchall()
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["ts_code", "close", "turnover_rate", "factor_name", "factor_value"])
        pivot = df.pivot_table(
            index="ts_code", columns="factor_name", values="factor_value", aggfunc="first"
        )
        pivot["close"] = df.groupby("ts_code")["close"].first()
        pivot["turnover_rate"] = df.groupby("ts_code")["turnover_rate"].first()
        return pivot

    def _get_next_returns(self, codes: List[str], today: date, nxt: date) -> List[float]:
        with get_db_session() as session:
            result = session.execute(
                text(
                    """
                    SELECT ts_code, close, pre_close
                    FROM daily_price
                    WHERE ts_code IN :codes AND trade_date = :date
                    """
                ),
                {"codes": tuple(codes), "date": nxt},
            )
            rows = result.fetchall()
        if not rows:
            return []
        returns = []
        for code, close, pre_close in rows:
            if pre_close and pre_close > 0:
                returns.append(float(close / pre_close - 1))
        return returns

    def _statistical_tests(self, returns) -> dict:
        if returns is None or len(returns) < 8:
            return {"passed": False, "reason": f"样本量不足: {len(returns) if returns is not None else 0}"}
        t_stat, p_value = stats.ttest_1samp(returns, 0)
        if p_value > 0.15:
            return {"passed": False, "reason": f"收益不显著: p={p_value:.3f}", "p_value": p_value}
        return {"passed": True, "p_value": round(float(p_value), 4)}
