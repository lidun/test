"""策略编译器：将策略字典编译为可执行的选股函数"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.strategy.schema import ConditionOperator, Strategy


class StrategyCompiler:
    def __init__(self):
        self.operator_funcs = {
            ConditionOperator.GT: lambda x, v: x > v,
            ConditionOperator.LT: lambda x, v: x < v,
            ConditionOperator.GTE: lambda x, v: x >= v,
            ConditionOperator.LTE: lambda x, v: x <= v,
            ConditionOperator.EQ: lambda x, v: x == v,
            ConditionOperator.BETWEEN: lambda x, v: (x >= v[0]) & (x <= v[1]),
            # 简化处理：CROSS_ABOVE/BELOW 退化为当前值比较（需连续值），
            # 完整实现需要前一日数据，在 data 中提供 prev_<factor> 列时启用
            ConditionOperator.CROSS_ABOVE: lambda x, v: (x > v) & (x.shift(1) <= v),
            ConditionOperator.CROSS_BELOW: lambda x, v: (x < v) & (x.shift(1) >= v),
        }

    def compile(self, strategy_dict: dict):
        """编译策略字典为可执行函数

        Returns:
            (success, message, strategy_func, strategy_obj)
        """
        try:
            strategy = Strategy.from_dict(strategy_dict)
        except Exception as e:
            return False, f"策略解析失败: {e}", None, None

        valid, errors = strategy.validate()
        if not valid:
            return False, f"策略验证失败: {'; '.join(errors)}", None, None

        valid, msg = self._validate_factors(strategy)
        if not valid:
            return False, msg, None, None

        strategy_func = self._generate_function(strategy)
        return True, "编译成功", strategy_func, strategy

    def _validate_factors(self, strategy: Strategy):
        """校验条件因子与排名因子是否在已知因子库中"""
        known = {
            "ma_20", "ma_60", "rsi_14", "macd_dif", "macd_hist", "boll_width",
            "close_ma_20", "close_ma_60", "ret_5d", "ret_20d", "ret_60d",
            "volatility_20d", "max_drawdown_60d", "volume_ratio", "volume_ratio_5d",
            "turnover_rate", "pe_ttm", "pb", "roe", "dividend_yield",
            "revenue_growth", "profit_growth", "net_inflow_large", "north_inflow",
        }
        unknown = []
        for cond in strategy.conditions:
            if cond.factor not in known:
                unknown.append(cond.factor)
        for rf in strategy.ranking_factors:
            if rf.factor not in known:
                unknown.append(rf.factor)
        if unknown:
            return False, f"未知因子: {', '.join(set(unknown))}"
        return True, ""

    def _generate_function(self, strategy: Strategy):
        def select_stocks(data: pd.DataFrame) -> pd.DataFrame:
            if data is None or len(data) == 0:
                return pd.DataFrame()
            df = data.copy()

            # 1. 应用筛选条件
            mask = pd.Series(True, index=df.index)
            for cond in strategy.conditions:
                factor = cond.factor
                if factor not in df.columns:
                    continue
                op_func = self.operator_funcs.get(cond.operator)
                if op_func is None:
                    continue
                try:
                    factor_values = pd.to_numeric(df[factor], errors="coerce")
                    mask &= op_func(factor_values, cond.value).fillna(False)
                except (TypeError, ValueError):
                    continue

            filtered = df[mask].copy()
            if len(filtered) == 0:
                return pd.DataFrame()

            # 2. 打分排序
            if strategy.ranking_factors:
                filtered["_score"] = 0.0
                for rf in strategy.ranking_factors:
                    if rf.factor not in filtered.columns:
                        continue
                    values = pd.to_numeric(filtered[rf.factor], errors="coerce")
                    if values.dropna().empty:
                        continue
                    zscore = (values - values.mean()) / (values.std() + 1e-10)
                    if rf.direction == "ascending":
                        zscore = -zscore
                    filtered["_score"] += zscore.fillna(0) * rf.weight
                filtered = filtered.sort_values("_score", ascending=False).head(
                    strategy.max_position
                )
                filtered = filtered.drop(columns=["_score"])
            else:
                filtered = filtered.head(strategy.max_position)
            return filtered

        return select_stocks
