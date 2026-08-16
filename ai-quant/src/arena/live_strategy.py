"""活跃策略：单策略在模拟环境中的运行实体"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np

from src.core.config import config
from src.strategy.schema import StrategyStatus


@dataclass
class Position:
    ts_code: str
    shares: int
    avg_cost: float
    current_price: float


@dataclass
class Trade:
    ts_code: str
    direction: str  # buy | sell
    price: float
    shares: int
    trade_date: str
    reason: str = ""


class LiveStrategy:
    def __init__(
        self,
        strategy_id,
        strategy_func,
        strategy_meta,
        initial_capital=None,
        commission_rate=None,
        slippage=None,
    ):
        self.strategy_id = strategy_id
        self.strategy_func = strategy_func
        self.meta = strategy_meta
        self.name = strategy_meta.get("name", strategy_id)
        self.initial_capital = initial_capital or config.strategy.initial_capital
        self.cash = self.initial_capital
        self.total_value = self.initial_capital
        self.commission_rate = commission_rate or config.strategy.commission_rate
        self.slippage = slippage or config.strategy.slippage

        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
        self.nav_history: List[Dict] = []
        self.stop_loss = strategy_meta.get("stop_loss", -0.08)
        self.stop_profit = strategy_meta.get("stop_profit", 0.30)
        self.max_position = strategy_meta.get("max_position", 10)
        self.rebalance_freq = strategy_meta.get("rebalance_freq", "weekly")
        self.single_stock_weight = strategy_meta.get("single_stock_weight", 0.1)
        self._last_rebalance = None
        self.status = StrategyStatus.ACTIVE

    def update_daily(self, market_data, trade_date, prices):
        self._update_positions(prices)
        self._check_stop_loss_take_profit(prices, trade_date)
        if self._should_rebalance(trade_date):
            self._rebalance(market_data, prices, trade_date)
            self._last_rebalance = trade_date
        self._update_total_value(prices)
        prev_nav = self.nav_history[-1]["nav"] if self.nav_history else self.initial_capital
        daily_return = (self.total_value / prev_nav - 1) if prev_nav else 0
        self.nav_history.append(
            {
                "date": trade_date,
                "nav": self.total_value,
                "daily_return": daily_return,
            }
        )

    def _should_rebalance(self, trade_date) -> bool:
        if self._last_rebalance is None:
            return True
        # trade_date 为 datetime.date 或 str
        if self.rebalance_freq == "daily":
            return True
        try:
            from datetime import datetime

            if isinstance(trade_date, str):
                td = datetime.strptime(trade_date, "%Y-%m-%d").date()
            else:
                td = trade_date
            if self.rebalance_freq == "monthly":
                return td.month != self._last_rebalance.month
            return (td - self._last_rebalance).days >= 7
        except (TypeError, ValueError):
            return True

    def _update_positions(self, prices: Dict[str, float]):
        for code, pos in self.positions.items():
            if code in prices and prices[code] > 0:
                pos.current_price = prices[code]

    def _check_stop_loss_take_profit(self, prices, trade_date):
        for code in list(self.positions.keys()):
            pos = self.positions[code]
            if pos.current_price <= 0:
                continue
            ret = pos.current_price / pos.avg_cost - 1
            if ret <= self.stop_loss:
                self._sell(code, pos.current_price, trade_date, "止损")
            elif ret >= self.stop_profit:
                self._sell(code, pos.current_price, trade_date, "止盈")

    def _rebalance(self, market_data, prices, trade_date):
        new_picks = self.strategy_func(market_data)
        if new_picks is None or len(new_picks) == 0:
            return "未选出股票"
        new_codes = set(new_picks.index.tolist())
        current_codes = set(self.positions.keys())

        for code in current_codes - new_codes:
            if code in prices:
                self._sell(code, prices.get(code, 0), trade_date, "调仓卖出")

        n_buy = len(new_codes)
        target_weight = min(1.0 / n_buy, self.single_stock_weight)
        bought = 0
        for code in new_codes - current_codes:
            price = prices.get(code, 0)
            if price > 0:
                shares = int(self.total_value * target_weight / price / 100) * 100
                if shares > 0 and self.cash >= shares * price * 1.01:
                    self._buy(code, price, shares, trade_date, "新买入")
                    bought += 1
        return f"调仓完成，买入 {bought} 只"

    def _buy(self, code, price, shares, trade_date, reason=""):
        buy_price = price * (1 + self.slippage)
        cost = shares * buy_price
        commission = cost * self.commission_rate
        total_cost = cost + commission
        if total_cost > self.cash * 0.95:
            return
        self.cash -= total_cost
        if code in self.positions:
            old = self.positions[code]
            total_shares = old.shares + shares
            avg_cost = (old.shares * old.avg_cost + cost) / total_shares
            self.positions[code] = Position(code, total_shares, avg_cost, buy_price)
        else:
            self.positions[code] = Position(code, shares, buy_price, buy_price)
        self.trades.append(Trade(code, "buy", buy_price, shares, str(trade_date), reason))

    def _sell(self, code, price, trade_date, reason=""):
        if code not in self.positions:
            return
        pos = self.positions[code]
        sell_price = price * (1 - self.slippage)
        proceeds = pos.shares * sell_price
        commission = proceeds * self.commission_rate
        self.cash += proceeds - commission
        self.trades.append(Trade(code, "sell", sell_price, pos.shares, str(trade_date), reason))
        del self.positions[code]

    def _update_total_value(self, prices):
        holdings = sum(
            pos.shares * pos.current_price for pos in self.positions.values()
        )
        self.total_value = self.cash + holdings

    def get_positions_df(self):
        import pandas as pd

        if not self.positions:
            return pd.DataFrame(
                columns=["ts_code", "shares", "avg_cost", "current_price", "pnl_pct"]
            )
        records = []
        for code, pos in self.positions.items():
            pnl_pct = pos.current_price / pos.avg_cost - 1 if pos.avg_cost else 0
            records.append(
                {
                    "ts_code": code,
                    "shares": pos.shares,
                    "avg_cost": round(pos.avg_cost, 3),
                    "current_price": round(pos.current_price, 3),
                    "market_value": round(pos.shares * pos.current_price, 2),
                    "pnl_pct": round(pnl_pct, 4),
                }
            )
        return pd.DataFrame(records)

    def get_trades_df(self):
        import pandas as pd

        if not self.trades:
            return pd.DataFrame(columns=["ts_code", "direction", "price", "shares", "trade_date", "reason"])
        return pd.DataFrame(
            [
                {
                    "ts_code": t.ts_code,
                    "direction": t.direction,
                    "price": t.price,
                    "shares": t.shares,
                    "trade_date": t.trade_date,
                    "reason": t.reason,
                }
                for t in self.trades
            ]
        )

    def get_stats(self) -> dict:
        stats = {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "total_value": round(self.total_value, 2),
            "positions": len(self.positions),
        }
        if len(self.nav_history) < 2:
            stats.update(
                {
                    "total_return": 0.0,
                    "sharpe": 0.0,
                    "max_drawdown": 0.0,
                    "win_rate": 0.0,
                    "trading_days": len(self.nav_history),
                }
            )
            return stats
        navs = [h["nav"] for h in self.nav_history]
        returns = np.array([navs[i] / navs[i - 1] - 1 for i in range(1, len(navs))])
        total_return = self.total_value / self.initial_capital - 1
        sharpe = returns.mean() / returns.std() * np.sqrt(252) if returns.std() > 0 else 0
        cum = np.cumprod(1 + returns)
        peak = np.maximum.accumulate(cum)
        max_dd = float(((cum - peak) / peak).min())
        win_rate = float((returns > 0).mean())
        stats.update(
            {
                "total_return": round(float(total_return), 4),
                "sharpe": round(float(sharpe), 4),
                "max_drawdown": round(max_dd, 4),
                "win_rate": round(win_rate, 4),
                "trading_days": len(returns),
                "cash": round(self.cash, 2),
            }
        )
        return stats
