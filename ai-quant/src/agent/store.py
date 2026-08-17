"""Agent 存储层：Agent/记忆/对话/持仓/成交/绩效/定时任务 的数据库读写"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from pathlib import Path

from loguru import logger
from sqlalchemy import text

from src.core.database import get_db_session
from src.agent.models import (
    OVERSEER_AGENT_ID,
    Agent,
    AgentTask,
    MemoryItem,
    Position,
    Trade,
)


def _agent_from_row(row) -> Agent:
    return Agent(
        id=row[0],
        name=row[1],
        description=row[2] or "",
        system_prompt=row[3] or "",
        llm_provider=row[4] or "deepseek",
        llm_api_key=row[5] or "",
        llm_base_url=row[6] or "",
        llm_model=row[7] or "",
        status=row[8] or "running",
        is_overseer=bool(row[9] or False),
        skills=row[10] or "",
        initial_capital=float(row[11] or 0),
        current_cash=float(row[12] or 0),
        max_position=int(row[13] or 10),
        single_stock_weight=float(row[14] or 0.1),
        commission_rate=float(row[15] or 0.0003),
        slippage=float(row[16] or 0.001),
        created_at=row[17],
        updated_at=row[18],
        last_active_at=row[19],
    )


_AGENT_COLUMNS = (
    "id, name, description, system_prompt, llm_provider, llm_api_key, "
    "llm_base_url, llm_model, status, is_overseer, skills, initial_capital, "
    "current_cash, max_position, single_stock_weight, commission_rate, slippage, "
    "created_at, updated_at, last_active_at"
)


class AgentStore:
    # ---------------- Agent CRUD ----------------

    def __init__(self):
        self._file_store = None

    def create_agent(
        self,
        name: str,
        description: str = "",
        system_prompt: str = "",
        llm_provider: str = "deepseek",
        llm_api_key: str = "",
        llm_base_url: str = "",
        llm_model: str = "",
        initial_capital: float = 100000.0,
        **kwargs,
    ) -> Agent:
        agent_id = kwargs.get("agent_id") or "agent_" + uuid.uuid4().hex[:10]
        skills = kwargs.get("skills", "")
        is_overseer = bool(kwargs.get("is_overseer", False))
        with get_db_session() as session:
            session.execute(
                text(
                    f"""
                    INSERT INTO agent (id, name, description, system_prompt, llm_provider,
                        llm_api_key, llm_base_url, llm_model, status, is_overseer, skills,
                        initial_capital, current_cash, max_position, single_stock_weight,
                        commission_rate, slippage)
                    VALUES (:id, :name, :description, :system_prompt, :llm_provider,
                        :llm_api_key, :llm_base_url, :llm_model, 'running', :overseer, :skills,
                        :capital, :capital, :max_pos, :weight, :commission, :slippage)
                    """
                ),
                {
                    "id": agent_id,
                    "name": name,
                    "description": description,
                    "system_prompt": system_prompt,
                    "llm_provider": llm_provider,
                    "llm_api_key": llm_api_key,
                    "llm_base_url": llm_base_url,
                    "llm_model": llm_model,
                    "overseer": is_overseer,
                    "skills": skills,
                    "capital": float(initial_capital),
                    "max_pos": int(kwargs.get("max_position", 10)),
                    "weight": float(kwargs.get("single_stock_weight", 0.1)),
                    "commission": float(kwargs.get("commission_rate", 0.0003)),
                    "slippage": float(kwargs.get("slippage", 0.001)),
                },
            )
        agent = self.get_agent(agent_id)
        logger.info(f"创建 Agent: {name} ({agent_id})")
        return agent

    def ensure_system_agent(self) -> Agent:
        """确保内置统筹 Agent 存在（不可删除，全局汇总与协作）"""
        existing = self.get_agent(OVERSEER_AGENT_ID)
        if existing:
            return existing
        from src.agent.skills import OVERSEER_PROMPT

        return self.create_agent(
            agent_id=OVERSEER_AGENT_ID,
            name="统筹总管",
            description="系统统筹 Agent：汇总对比各 Agent 表现，把控全局，协助各 Agent 模拟交易",
            system_prompt=OVERSEER_PROMPT,
            llm_provider="deepseek",
            is_overseer=True,
            skills="overseer",
            initial_capital=100000.0,
        )

    def get_agent(self, agent_id: str) -> Agent | None:
        with get_db_session() as session:
            row = session.execute(
                text(f"SELECT {_AGENT_COLUMNS} FROM agent WHERE id = :id"),
                {"id": agent_id},
            ).fetchone()
        return _agent_from_row(row) if row else None

    def list_agents(self, include_archived: bool = False) -> list[Agent]:
        sql = f"SELECT {_AGENT_COLUMNS} FROM agent"
        if not include_archived:
            sql += " WHERE status != 'archived'"
        sql += " ORDER BY is_overseer DESC, created_at"
        with get_db_session() as session:
            rows = session.execute(text(sql)).fetchall()
        return [_agent_from_row(r) for r in rows]

    def update_agent(self, agent_id: str, **fields) -> Agent | None:
        allowed = {
            "name", "description", "system_prompt", "llm_provider",
            "llm_api_key", "llm_base_url", "llm_model", "status",
            "max_position", "single_stock_weight", "commission_rate", "slippage",
            "current_cash", "skills",
        }
        sets = []
        params: dict = {"id": agent_id}
        for key, val in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key} = :{key}")
            params[key] = val
        if not sets:
            return self.get_agent(agent_id)
        sets.append("updated_at = NOW()")
        with get_db_session() as session:
            session.execute(
                text(f"UPDATE agent SET {', '.join(sets)} WHERE id = :id"),
                params,
            )
        return self.get_agent(agent_id)

    def delete_agent(self, agent_id: str) -> bool:
        if agent_id == OVERSEER_AGENT_ID:
            logger.warning("统筹 Agent 不允许删除")
            return False
        with get_db_session() as session:
            session.execute(text("DELETE FROM agent_memory WHERE agent_id = :id"), {"id": agent_id})
            session.execute(text("DELETE FROM agent_chat WHERE agent_id = :id"), {"id": agent_id})
            session.execute(text("DELETE FROM agent_position WHERE agent_id = :id"), {"id": agent_id})
            session.execute(text("DELETE FROM agent_trade WHERE agent_id = :id"), {"id": agent_id})
            session.execute(text("DELETE FROM agent_performance WHERE agent_id = :id"), {"id": agent_id})
            session.execute(text("DELETE FROM agent_task WHERE agent_id = :id"), {"id": agent_id})
            result = session.execute(text("DELETE FROM agent WHERE id = :id"), {"id": agent_id})
        return result.rowcount > 0

    def touch_agent(self, agent_id: str):
        with get_db_session() as session:
            session.execute(
                text("UPDATE agent SET last_active_at = NOW(), updated_at = NOW() WHERE id = :id"),
                {"id": agent_id},
            )

    # ---------------- 长期记忆 ----------------

    def add_memory(self, agent_id: str, content: str, memory_type: str = "experience") -> MemoryItem:
        with get_db_session() as session:
            row = session.execute(
                text(
                    "INSERT INTO agent_memory (agent_id, content, memory_type) "
                    "VALUES (:aid, :content, :type) RETURNING id, agent_id, content, memory_type, created_at"
                ),
                {"aid": agent_id, "content": content, "type": memory_type},
            ).fetchone()
        # 双写：同步追加到 Agent 专属记忆文件
        try:
            self.file_store.append_memory(agent_id, content)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"记忆文件写入失败: {e}")
        return MemoryItem(*row)

    @property
    def file_store(self) -> "AgentFileStore":
        if self._file_store is None:
            self._file_store = AgentFileStore()
        return self._file_store

    def list_memories(self, agent_id: str, limit: int = 50) -> list[MemoryItem]:
        with get_db_session() as session:
            rows = session.execute(
                text(
                    "SELECT id, agent_id, content, memory_type, created_at FROM agent_memory "
                    "WHERE agent_id = :aid ORDER BY id DESC LIMIT :lim"
                ),
                {"aid": agent_id, "lim": limit},
            ).fetchall()
        return [MemoryItem(*r) for r in rows]

    def recall_memories(self, agent_id: str, query: str = "", limit: int = 10) -> list[MemoryItem]:
        """检索长期记忆：按关键词/全部（最近优先）"""
        with get_db_session() as session:
            if query:
                rows = session.execute(
                    text(
                        "SELECT id, agent_id, content, memory_type, created_at FROM agent_memory "
                        "WHERE agent_id = :aid AND content ILIKE :kw ORDER BY id DESC LIMIT :lim"
                    ),
                    {"aid": agent_id, "kw": f"%{query}%", "lim": limit},
                ).fetchall()
            else:
                rows = session.execute(
                    text(
                        "SELECT id, agent_id, content, memory_type, created_at FROM agent_memory "
                        "WHERE agent_id = :aid ORDER BY id DESC LIMIT :lim"
                    ),
                    {"aid": agent_id, "lim": limit},
                ).fetchall()
        return [MemoryItem(*r) for r in rows]

    # ---------------- 对话记录 ----------------

    def add_chat(self, agent_id: str, role: str, content: str):
        with get_db_session() as session:
            session.execute(
                text(
                    "INSERT INTO agent_chat (agent_id, role, content) VALUES (:aid, :role, :content)"
                ),
                {"aid": agent_id, "role": role, "content": content},
            )

    def list_chat(self, agent_id: str, limit: int = 50) -> list[dict]:
        with get_db_session() as session:
            rows = session.execute(
                text(
                    "SELECT role, content, created_at FROM agent_chat "
                    "WHERE agent_id = :aid ORDER BY id DESC LIMIT :lim"
                ),
                {"aid": agent_id, "lim": limit},
            ).fetchall()
        return [
            {"role": r[0], "content": r[1], "created_at": r[2].isoformat() if r[2] else None}
            for r in rows[::-1]
        ]

    # ---------------- 持仓 ----------------

    def get_positions(self, agent_id: str) -> list[Position]:
        with get_db_session() as session:
            rows = session.execute(
                text(
                    "SELECT agent_id, ts_code, shares, avg_cost, current_price "
                    "FROM agent_position WHERE agent_id = :aid ORDER BY ts_code"
                ),
                {"aid": agent_id},
            ).fetchall()
        return [
            Position(
                agent_id=r[0],
                ts_code=r[1],
                shares=int(r[2]),
                avg_cost=float(r[3] or 0),
                current_price=float(r[4]) if r[4] is not None else 0.0,
            )
            for r in rows
        ]

    def upsert_position(self, pos: Position):
        with get_db_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO agent_position (agent_id, ts_code, shares, avg_cost, current_price, updated_at)
                    VALUES (:aid, :code, :shares, :cost, :price, NOW())
                    ON CONFLICT (agent_id, ts_code) DO UPDATE SET
                        shares = EXCLUDED.shares, avg_cost = EXCLUDED.avg_cost,
                        current_price = EXCLUDED.current_price, updated_at = NOW()
                    """
                ),
                {
                    "aid": pos.agent_id,
                    "code": pos.ts_code,
                    "shares": pos.shares,
                    "cost": pos.avg_cost,
                    "price": pos.current_price,
                },
            )

    def delete_position(self, agent_id: str, ts_code: str):
        with get_db_session() as session:
            session.execute(
                text("DELETE FROM agent_position WHERE agent_id = :aid AND ts_code = :code"),
                {"aid": agent_id, "code": ts_code},
            )

    # ---------------- 成交 ----------------

    def add_trade(self, trade: Trade):
        with get_db_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO agent_trade (agent_id, ts_code, direction, price, shares, trade_date, reason)
                    VALUES (:aid, :code, :dir, :price, :shares, :td, :reason)
                    """
                ),
                {
                    "aid": trade.agent_id,
                    "code": trade.ts_code,
                    "dir": trade.direction,
                    "price": trade.price,
                    "shares": trade.shares,
                    "td": trade.trade_date,
                    "reason": trade.reason,
                },
            )

    def list_trades(self, agent_id: str, limit: int = 100) -> list[dict]:
        with get_db_session() as session:
            rows = session.execute(
                text(
                    "SELECT ts_code, direction, price, shares, trade_date, reason, created_at "
                    "FROM agent_trade WHERE agent_id = :aid ORDER BY id DESC LIMIT :lim"
                ),
                {"aid": agent_id, "lim": limit},
            ).fetchall()
        return [
            {
                "ts_code": r[0], "direction": r[1], "price": float(r[2]),
                "shares": r[3], "trade_date": r[4].isoformat() if r[4] else None,
                "reason": r[5], "created_at": r[6].isoformat() if r[6] else None,
            }
            for r in rows
        ]

    # ---------------- 绩效 ----------------

    def upsert_performance(self, agent_id: str, trade_date, nav, daily_return,
                           cumulative_return, positions_count, cash, total_value):
        with get_db_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO agent_performance
                        (agent_id, trade_date, nav, daily_return, cumulative_return,
                         positions_count, cash, total_value)
                    VALUES (:aid, :td, :nav, :dr, :cr, :pc, :cash, :tv)
                    ON CONFLICT (agent_id, trade_date) DO UPDATE SET
                        nav = EXCLUDED.nav, daily_return = EXCLUDED.daily_return,
                        cumulative_return = EXCLUDED.cumulative_return,
                        positions_count = EXCLUDED.positions_count,
                        cash = EXCLUDED.cash, total_value = EXCLUDED.total_value
                    """
                ),
                {
                    "aid": agent_id, "td": trade_date, "nav": nav, "dr": daily_return,
                    "cr": cumulative_return, "pc": positions_count, "cash": cash, "tv": total_value,
                },
            )

    def list_performance(self, agent_id: str, limit: int = 90) -> list[dict]:
        with get_db_session() as session:
            rows = session.execute(
                text(
                    "SELECT trade_date, nav, daily_return, cumulative_return, positions_count, total_value "
                    "FROM agent_performance WHERE agent_id = :aid ORDER BY trade_date DESC LIMIT :lim"
                ),
                {"aid": agent_id, "lim": limit},
            ).fetchall()
        rows = rows[::-1]
        return [
            {
                "trade_date": r[0].isoformat() if r[0] else None,
                "nav": r[1], "daily_return": r[2], "cumulative_return": r[3],
                "positions_count": r[4], "total_value": r[5],
            }
            for r in rows
        ]

    def rank_agents(self, limit: int = 50) -> list[dict]:
        """按最新累计收益对所有 Agent 排名"""
        with get_db_session() as session:
            rows = session.execute(
                text(
                    """
                    SELECT a.id, a.name, a.status,
                           ap.trade_date, ap.nav, ap.cumulative_return,
                           ap.positions_count, ap.total_value
                    FROM agent a
                    LEFT JOIN LATERAL (
                        SELECT trade_date, nav, cumulative_return, positions_count, total_value
                        FROM agent_performance
                        WHERE agent_id = a.id
                        ORDER BY trade_date DESC LIMIT 1
                    ) ap ON true
                    WHERE a.status != 'archived'
                    ORDER BY ap.cumulative_return DESC NULLS LAST
                    LIMIT :lim
                    """
                ),
                {"lim": limit},
            ).fetchall()
        return [
            {
                "agent_id": r[0], "name": r[1], "status": r[2],
                "trade_date": r[3].isoformat() if r[3] else None,
                "nav": r[4], "cumulative_return": r[5],
                "positions_count": r[6], "total_value": r[7],
            }
            for r in rows
        ]

    # ---------------- 定时任务 ----------------

    def add_task(self, agent_id: str, schedule_type: str = "daily",
                 schedule_time: str = "09:30", interval_hours: float = 0.0,
                 enabled: bool = True) -> AgentTask:
        with get_db_session() as session:
            row = session.execute(
                text(
                    "INSERT INTO agent_task (agent_id, schedule_type, schedule_time, interval_hours, enabled) "
                    "VALUES (:aid, :type, :time, :hours, :enabled) "
                    "RETURNING id, agent_id, schedule_type, schedule_time, interval_hours, enabled, last_run_at, created_at"
                ),
                {
                    "aid": agent_id, "type": schedule_type,
                    "time": schedule_time, "hours": interval_hours, "enabled": enabled,
                },
            ).fetchone()
        return AgentTask(*row)

    def list_tasks(self, agent_id: str) -> list[AgentTask]:
        with get_db_session() as session:
            rows = session.execute(
                text(
                    "SELECT id, agent_id, schedule_type, schedule_time, interval_hours, enabled, last_run_at, created_at "
                    "FROM agent_task WHERE agent_id = :aid ORDER BY id"
                ),
                {"aid": agent_id},
            ).fetchall()
        return [AgentTask(*r) for r in rows]

    def get_task(self, task_id: int) -> AgentTask | None:
        with get_db_session() as session:
            row = session.execute(
                text(
                    "SELECT id, agent_id, schedule_type, schedule_time, interval_hours, enabled, last_run_at, created_at "
                    "FROM agent_task WHERE id = :tid"
                ),
                {"tid": task_id},
            ).fetchone()
        return AgentTask(*row) if row else None

    def update_task(self, task_id: int, **fields) -> AgentTask | None:
        allowed = {"schedule_type", "schedule_time", "interval_hours", "enabled"}
        sets = []
        params: dict = {"tid": task_id}
        for key, val in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key} = :{key}")
            params[key] = val
        if sets:
            with get_db_session() as session:
                session.execute(
                    text(f"UPDATE agent_task SET {', '.join(sets)} WHERE id = :tid"),
                    params,
                )
        return self.get_task(task_id)

    def mark_task_run(self, task_id: int):
        with get_db_session() as session:
            session.execute(
                text("UPDATE agent_task SET last_run_at = NOW() WHERE id = :tid"),
                {"tid": task_id},
            )

    def delete_task(self, task_id: int) -> bool:
        with get_db_session() as session:
            result = session.execute(text("DELETE FROM agent_task WHERE id = :tid"), {"tid": task_id})
        return result.rowcount > 0


class AgentFileStore:
    """Agent 专属文件存储：data/agents/{agent_id}/ 目录

    - memory.md：长期记忆文件（追加式，与数据库 agent_memory 双写）
    - 产出文件：对话/研究中保存的笔记、报告等
    """

    def __init__(self):
        from src.core.config import config

        self.root = config.DATA_DIR / "agents"

    def dir_for(self, agent_id: str) -> Path:
        d = self.root / agent_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def memory_file(self, agent_id: str) -> Path:
        return self.dir_for(agent_id) / "memory.md"

    # ---------------- 记忆文件 ----------------

    def append_memory(self, agent_id: str, content: str) -> Path:
        f = self.memory_file(agent_id)
        entry = (
            f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')}\n{content}\n"
        )
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(entry)
        return f

    def read_memory(self, agent_id: str, limit_chars: int = 4000) -> str:
        f = self.memory_file(agent_id)
        if not f.exists():
            return ""
        text = f.read_text(encoding="utf-8", errors="ignore")
        if len(text) > limit_chars:
            text = "...(记忆较长，截断)...\n" + text[-limit_chars:]
        return text

    # ---------------- 产出文件 ----------------

    def save_file(self, agent_id: str, filename: str, content: str) -> Path:
        """保存 Agent 产出文件（文件名安全处理）"""
        safe = filename.replace("/", "_").replace("\\", "_").strip()
        if not safe:
            safe = f"note_{datetime.now().strftime('%Y%m%d%H%M%S')}.md"
        p = self.dir_for(agent_id) / safe
        p.write_text(content, encoding="utf-8")
        return p

    def list_files(self, agent_id: str) -> list[dict]:
        d = self.dir_for(agent_id)
        out = []
        for p in sorted(d.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.name.startswith("."):
                continue
            out.append(
                {
                    "name": p.name,
                    "size": p.stat().st_size,
                    "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
                }
            )
        return out

    def read_file(self, agent_id: str, filename: str) -> str | None:
        safe = filename.replace("/", "_").replace("\\", "_").strip()
        p = self.dir_for(agent_id) / safe
        if not p.exists() or not p.is_file():
            return None
        return p.read_text(encoding="utf-8", errors="ignore")
