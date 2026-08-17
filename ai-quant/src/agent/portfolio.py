"""Agent 模拟账户：独立持仓、买卖、估值与绩效记录"""
from __future__ import annotations

from datetime import date

import numpy as np
from loguru import logger
from sqlalchemy import text

from src.agent.models import Agent, Position, Trade
from src.agent.store import AgentStore
from src.core.database import get_db_session


class AgentPortfolio:
    """单个 Agent 的模拟交易账户（彼此完全独立）"""

    def __init__(self, agent: Agent, store: AgentStore | None = None):
        self.agent = agent
        self.store = store or AgentStore()

    def _fresh_agent(self) -> Agent:
        """从数据库读取最新 Agent（交易后现金/参数会变化）"""
        fresh = self.store.get_agent(self.agent.id)
        if fresh:
            self.agent = fresh
        return self.agent

    # ---------------- 价格 ----------------

    @staticmethod
    def latest_price(ts_code: str) -> float | None:
        with get_db_session() as session:
            val = session.execute(
                text(
                    "SELECT close FROM daily_price WHERE ts_code = :code "
                    "ORDER BY trade_date DESC LIMIT 1"
                ),
                {"code": ts_code},
            ).scalar()
        return float(val) if val else None

    @staticmethod
    def latest_trade_date() -> date | None:
        with get_db_session() as session:
            d = session.execute(text("SELECT MAX(trade_date) FROM daily_price")).scalar()
        return d if isinstance(d, date) else None

    @staticmethod
    def stock_name(ts_code: str) -> str:
        with get_db_session() as session:
            row = session.execute(
                text("SELECT name FROM stock_basic WHERE ts_code = :code"),
                {"code": ts_code},
            ).fetchone()
        return row[0] if row else ts_code

    # ---------------- 交易 ----------------

    def buy(self, ts_code: str, shares: int, reason: str = "", trade_date: str | None = None):
        """以最新收盘价买入，返回结果消息"""
        if shares <= 0:
            return {"ok": False, "message": "买入数量必须为正"}
        price = self.latest_price(ts_code)
        if not price or price <= 0:
            return {"ok": False, "message": f"{ts_code} 无行情数据，无法交易"}
        td = trade_date or date.today().isoformat()
        self._fresh_agent()
        agent = self.store.get_agent(self.agent.id)
        if agent is None:
            return {"ok": False, "message": "Agent 不存在"}
        cash = agent.current_cash
        buy_price = price * (1 + self.agent.slippage)
        cost = shares * buy_price
        commission = cost * self.agent.commission_rate
        total_cost = cost + commission
        if total_cost > cash:
            return {
                "ok": False,
                "message": f"现金不足：需要 {total_cost:.2f}，可用 {cash:.2f}",
            }
        # 限制单只权重
        limit = self.agent.single_stock_weight * self.agent.initial_capital
        if total_cost > limit:
            shares = max(100, int(limit // (buy_price * (1 + self.agent.commission_rate)) // 100 * 100))
            cost = shares * buy_price
            commission = cost * self.agent.commission_rate
            total_cost = cost + commission
        if shares <= 0:
            return {"ok": False, "message": "单只权重限制下无法买入"}
        cash -= total_cost
        self.store.update_agent(agent.id, current_cash=cash)
        pos = Position(agent.id, ts_code, shares, buy_price, price)
        old = next((p for p in self.store.get_positions(agent.id) if p.ts_code == ts_code), None)
        if old:
            total = old.shares + shares
            avg = (old.shares * old.avg_cost + cost) / total
            pos = Position(agent.id, ts_code, total, avg, price)
        self.store.upsert_position(pos)
        self.store.add_trade(
            Trade(agent.id, ts_code, "buy", buy_price, shares, td, reason)
        )
        return {
            "ok": True,
            "message": f"买入 {self.stock_name(ts_code)}({ts_code}) {shares} 股 @ {buy_price:.3f}，共 {total_cost:.2f}，剩余现金 {cash:.2f}",
        }

    def sell(self, ts_code: str, shares: int | None = None, reason: str = "", trade_date: str | None = None):
        """卖出，shares 为 None 时全部卖出"""
        positions = self.store.get_positions(self.agent.id)
        pos = next((p for p in positions if p.ts_code == ts_code), None)
        if pos is None:
            return {"ok": False, "message": f"未持有 {ts_code}"}
        price = self.latest_price(ts_code)
        if not price or price <= 0:
            return {"ok": False, "message": f"{ts_code} 无行情数据，无法卖出"}
        td = trade_date or date.today().isoformat()
        sell_shares = pos.shares if shares is None else min(shares, pos.shares)
        sell_price = price * (1 - self.agent.slippage)
        proceeds = sell_shares * sell_price
        commission = proceeds * self.agent.commission_rate
        self._fresh_agent()
        agent = self.store.get_agent(self.agent.id)
        if agent is None:
            return {"ok": False, "message": "Agent 不存在"}
        cash = agent.current_cash + proceeds - commission
        self.store.update_agent(agent.id, current_cash=cash)
        self.store.add_trade(
            Trade(agent.id, ts_code, "sell", sell_price, sell_shares, td, reason)
        )
        remaining = pos.shares - sell_shares
        if remaining > 0:
            pos.shares = remaining
            self.store.upsert_position(pos)
        else:
            self.store.delete_position(agent.id, ts_code)
        pnl = (sell_price - pos.avg_cost) * sell_shares
        return {
            "ok": True,
            "message": f"卖出 {self.stock_name(ts_code)}({ts_code}) {sell_shares} 股 @ {sell_price:.3f}，"
                       f"实现盈亏 {pnl:+.2f}，剩余现金 {cash:.2f}",
        }

    # ---------------- 估值与绩效 ----------------

    def mark_to_market(self, trade_date: str | None = None) -> dict:
        """按最新收盘价更新持仓估值，记录当日绩效"""
        self._fresh_agent()
        positions = self.store.get_positions(self.agent.id)
        total_value = self.agent.current_cash
        latest_dates: dict[str, str] = {}
        for pos in positions:
            price = self.latest_price(pos.ts_code)
            if price and price > 0:
                pos.current_price = price
                self.store.upsert_position(pos)
                total_value += pos.shares * price
        td = trade_date or date.today().isoformat()
        prev = self.store.list_performance(self.agent.id, limit=2)
        prev_nav = prev[-1]["nav"] if prev else None
        daily_return = (total_value / prev_nav - 1) if prev_nav else 0.0
        cumulative_return = total_value / self.agent.initial_capital - 1
        self.store.upsert_performance(
            agent_id=self.agent.id,
            trade_date=td,
            nav=round(total_value, 2),
            daily_return=round(daily_return, 6),
            cumulative_return=round(cumulative_return, 6),
            positions_count=len(positions),
            cash=round(self.agent.current_cash, 2),
            total_value=round(total_value, 2),
        )
        self.store.touch_agent(self.agent.id)
        return {
            "total_value": round(total_value, 2),
            "cash": round(self.agent.current_cash, 2),
            "positions_count": len(positions),
            "cumulative_return": round(cumulative_return, 6),
            "daily_return": round(daily_return, 6),
            "trade_date": td,
        }

    def summary(self) -> dict:
        """当前账户快照（供 LLM 工具/前端展示）"""
        self._fresh_agent()
        positions = self.store.get_positions(self.agent.id)
        total_value = self.agent.current_cash
        for pos in positions:
            price = pos.current_price or self.latest_price(pos.ts_code) or 0
            total_value += pos.shares * price
        cumulative_return = total_value / self.agent.initial_capital - 1
        rows = []
        for pos in positions:
            rows.append(
                {
                    "ts_code": pos.ts_code,
                    "name": self.stock_name(pos.ts_code),
                    "shares": pos.shares,
                    "avg_cost": round(pos.avg_cost, 3),
                    "current_price": round(pos.current_price or 0, 3),
                    "market_value": round(pos.market_value, 2),
                    "pnl_pct": round(pos.pnl_pct, 4),
                }
            )
        return {
            "agent_id": self.agent.id,
            "name": self.agent.name,
            "cash": round(self.agent.current_cash, 2),
            "positions": rows,
            "positions_count": len(positions),
            "total_value": round(total_value, 2),
            "cumulative_return": round(cumulative_return, 6),
        }

    def equity_curve(self, limit: int = 120) -> list[dict]:
        return self.store.list_performance(self.agent.id, limit=limit)
