"""Web 可视化面板：FastAPI 应用"""
from __future__ import annotations

import json
import secrets
from datetime import date, datetime
from decimal import Decimal

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response


class SafeJSONResponse(JSONResponse):
    """支持 date/datetime/Decimal 序列化的 JSONResponse"""

    def render(self, content) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
            default=self._default,
        ).encode("utf-8")

    @staticmethod
    def _default(o):
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        if isinstance(o, Decimal):
            return float(o)
        raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")

from src.core.config import config
from src.web.context import AppContext

app = FastAPI(title="AI自主进化选股系统", version="1.0")

BASE_DIR = config.PROJECT_ROOT / "src" / "web"
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ---------------- 基础认证 ----------------
@app.middleware("http")
async def require_auth(request: StarletteRequest, call_next):
    if request.url.path == "/api/health":
        return await call_next(request)
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return _unauthorized()
    import base64

    try:
        decoded = base64.b64decode(auth[6:]).decode("utf-8")
        user, _, pwd = decoded.partition(":")
    except Exception:
        return _unauthorized()
    if not secrets.compare_digest(user, config.web.username) or not secrets.compare_digest(
        pwd, config.web.password
    ):
        return _unauthorized()
    return await call_next(request)


def _unauthorized() -> Response:
    return SafeJSONResponse(
        {"detail": "Unauthorized"},
        status_code=401,
        headers={"WWW-Authenticate": "Basic realm=\"AI Quant\""},
    )


def ctx() -> AppContext:
    return AppContext.get()


# ---------------- 页面路由 ----------------
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    arena = ctx().arena
    metrics = ctx().metrics
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "now": datetime.now(),
            "provider": ctx().generator.client.name if ctx().generator else "unknown",
            "provider_model": ctx().generator.client.model if ctx().generator else "",
            "provider_available": ctx().generator.available if ctx().generator else False,
        },
    )


@app.get("/api/leaderboard")
async def api_leaderboard(metric: str = "sharpe", limit: int = 20):
    leaderboard = ctx().arena.get_leaderboard(metric=metric)
    records = leaderboard.head(limit).to_dict("records")
    # 日期等字段 JSON 序列化
    return SafeJSONResponse(records)


@app.get("/api/arena/stats")
async def api_arena_stats():
    return SafeJSONResponse(ctx().arena.get_arena_stats())


@app.get("/api/strategy/{strategy_id}")
async def api_strategy_detail(strategy_id: str):
    strategy = ctx().arena.get_strategy(strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="not_found")
    return SafeJSONResponse(
        {
            "info": strategy.meta,
            "stats": strategy.get_stats(),
            "positions": strategy.get_positions_df().to_dict("records"),
            "trades": strategy.get_trades_df().tail(50).to_dict("records"),
            "nav_history": strategy.nav_history,
        }
    )


@app.get("/api/strategies")
async def api_strategies():
    arena = ctx().arena
    records = []
    for sid, s in arena.strategies.items():
        records.append(
            {
                "strategy_id": sid,
                "name": s.name,
                "type": s.meta.get("type", "hybrid"),
                "generation": s.meta.get("generation", 0),
                "status": s.status.value if hasattr(s.status, "value") else str(s.status),
            }
        )
    return SafeJSONResponse(records)


@app.get("/api/benchmark")
async def api_benchmark():
    bench = ctx().arena.get_benchmark()
    return SafeJSONResponse(bench.to_dict("records"))


@app.get("/api/health")
async def api_health():
    health = ctx().metrics.health_check()
    health["system"] = ctx().metrics.collect_system_metrics().to_dict()
    return SafeJSONResponse(health)


@app.get("/api/system/metrics")
async def api_system_metrics():
    return SafeJSONResponse(ctx().metrics.collect_system_metrics().to_dict())


@app.get("/api/evolution/history")
async def api_evolution_history(limit: int = 20):
    from sqlalchemy import text

    from src.core.database import get_db_session

    with get_db_session() as session:
        rows = session.execute(
            text(
                """
                SELECT cycle, timestamp, eliminated_count, mutated_count,
                       crossover_count, new_count, added_count, arena_size
                FROM evolution_log ORDER BY cycle DESC LIMIT :limit
                """
            ),
            {"limit": limit},
        ).fetchall()
    return SafeJSONResponse(
        [
            {
                "cycle": r[0],
                "timestamp": r[1].isoformat() if r[1] else None,
                "eliminated": r[2],
                "mutated": r[3],
                "crossover": r[4],
                "new": r[5],
                "added": r[6],
                "arena_size": r[7],
            }
            for r in rows
        ]
    )


@app.get("/api/backtest/{strategy_id}")
async def api_backtest(strategy_id: str, days: int = 120):
    """策略净值曲线（用于图表展示）"""
    from sqlalchemy import text

    from src.core.database import get_db_session

    with get_db_session() as session:
        rows = session.execute(
            text(
                """
                SELECT trade_date, nav, cumulative_return FROM strategy_performance
                WHERE strategy_id = :sid ORDER BY trade_date DESC LIMIT :days
                """
            ),
            {"sid": strategy_id, "days": days},
        ).fetchall()
    rows = rows[::-1]
    return SafeJSONResponse(
        [
            {
                "date": r[0].isoformat() if r[0] else None,
                "nav": r[1],
                "cumulative_return": r[2],
            }
            for r in rows
        ]
    )


@app.post("/api/evolution/trigger")
async def api_trigger_evolution():
    from src.core.config import setup_logging

    summary = await ctx().evolution.evolve()
    return SafeJSONResponse(summary)


@app.get("/api/llm/providers")
async def api_llm_providers():
    cfg = config.llm
    providers = {}
    for name, pc in cfg.providers.items():
        providers[name] = {"available": pc.available, "model": pc.model}
    return SafeJSONResponse(
        {
            "default_provider": cfg.default_provider,
            "providers": providers,
            "generator_using": ctx().generator.client.name if ctx().generator else "none",
            "generator_available": ctx().generator.available if ctx().generator else False,
        }
    )


def init_web_app(arena, evolution, metrics, report_generator):
    """填充全局上下文（供 main.py 调用）"""
    ac = AppContext.get()
    ac.arena = arena
    ac.evolution = evolution
    ac.metrics = metrics
    ac.reporter = report_generator
