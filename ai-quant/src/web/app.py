"""Web 可视化面板：FastAPI 应用（交易 Agent 体系）"""
from __future__ import annotations

import json
import secrets
from datetime import date, datetime
from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from src.core.config import config
from src.web.context import AppContext


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


app = FastAPI(title="AI 交易 Agent 系统", version="2.0")

BASE_DIR = config.PROJECT_ROOT / "src" / "web"
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ---------------- 基础认证 ----------------
# 页面与功能 API（Agent/配置/健康/市场查询）免认证便于浏览器直接预览；
# 其余写操作仍需 Basic 认证。
@app.middleware("http")
async def require_auth(request, call_next):
    if request.method == "GET":
        return await call_next(request)
    if request.url.path.startswith(
        ("/api/agents", "/api/config", "/api/llm", "/api/health", "/api/market")
    ):
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


def _unauthorized() -> JSONResponse:
    return SafeJSONResponse(
        {"detail": "Unauthorized"},
        status_code=401,
        headers={"WWW-Authenticate": "Basic realm=\"AI Quant\""},
    )


def ctx() -> AppContext:
    return AppContext.get()


def _sse(event: dict) -> str:
    data = {k: v for k, v in event.items() if k != "type"}
    return f"event: {event['type']}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


# ---------------- 页面路由 ----------------
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "now": datetime.now(),
            "default_provider": config.llm.default_provider,
        },
    )


# ---------------- 市场查询 ----------------
@app.get("/api/market/quote")
async def api_market_quote(symbol: str):
    from src.agent.tools import get_stock_quote

    from src.agent.store import AgentStore
    from src.agent.portfolio import AgentPortfolio

    store = AgentStore()
    portfolio = AgentPortfolio(None, store=store)
    text = get_stock_quote(portfolio, store, {"symbol": symbol})
    return SafeJSONResponse({"symbol": symbol, "text": text})


@app.get("/api/market/stocks")
async def api_market_stocks(keyword: str = ""):
    from sqlalchemy import text

    from src.core.database import get_db_session

    kw = f"%{keyword}%"
    with get_db_session() as session:
        rows = session.execute(
            text(
                "SELECT ts_code, symbol, name, industry FROM stock_basic "
                "WHERE name ILIKE :kw OR ts_code ILIKE :kw OR symbol ILIKE :kw "
                "ORDER BY ts_code LIMIT 30"
            ),
            {"kw": kw},
        ).fetchall()
    return SafeJSONResponse(
        [{"ts_code": r[0], "symbol": r[1], "name": r[2], "industry": r[3]} for r in rows]
    )


# ---------------- Agent CRUD ----------------
@app.get("/api/agents")
async def api_agents():
    store = ctx().store
    agents = store.list_agents()
    rank = store.rank_agents(limit=100)
    rank_map = {r["agent_id"]: r for r in rank}
    out = []
    for a in agents:
        r = rank_map.get(a.id, {})
        out.append(
            {
                "id": a.id,
                "name": a.name,
                "description": a.description,
                "status": a.status,
                "is_overseer": a.is_overseer,
                "llm_provider": a.llm_provider,
                "llm_model": a.llm_model,
                "initial_capital": a.initial_capital,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "last_active_at": a.last_active_at.isoformat() if a.last_active_at else None,
                "cumulative_return": r.get("cumulative_return"),
                "total_value": r.get("total_value"),
                "nav": r.get("nav"),
                "positions_count": r.get("positions_count"),
                "rank": r.get("rank"),
            }
        )
    return SafeJSONResponse(out)


@app.post("/api/agents")
async def api_agents_create(payload: dict):
    store = ctx().store
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name 不能为空")
    agent = store.create_agent(
        name=name,
        description=str(payload.get("description") or ""),
        system_prompt=str(payload.get("system_prompt") or ""),
        llm_provider=str(payload.get("llm_provider") or "deepseek"),
        llm_api_key=str(payload.get("llm_api_key") or ""),
        llm_base_url=str(payload.get("llm_base_url") or ""),
        llm_model=str(payload.get("llm_model") or ""),
        initial_capital=float(payload.get("initial_capital") or 100000),
        max_position=int(payload.get("max_position") or 10),
        single_stock_weight=float(payload.get("single_stock_weight") or 0.1),
    )
    ctx().task_scheduler.sync_all()
    return SafeJSONResponse({"agent": _agent_public(agent), "message": "Agent 创建成功"})


@app.get("/api/agents/ranking")
async def api_agents_ranking(limit: int = 50):
    ranks = ctx().store.rank_agents(limit=limit)
    for i, r in enumerate(ranks, start=1):
        r["rank"] = i
    return SafeJSONResponse(ranks)


@app.get("/api/agents/{agent_id}")
async def api_agent_detail(agent_id: str):
    store = ctx().store
    agent = store.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    from src.agent.portfolio import AgentPortfolio

    portfolio = AgentPortfolio(agent, store)
    detail = _agent_public(agent)
    detail["portfolio"] = portfolio.summary()
    detail["memories"] = [
        {"id": m.id, "content": m.content, "memory_type": m.memory_type,
         "created_at": m.created_at.isoformat() if m.created_at else None}
        for m in store.list_memories(agent_id)
    ]
    detail["tasks"] = [
        {"id": t.id, "schedule_type": t.schedule_type, "schedule_time": t.schedule_time,
         "interval_hours": t.interval_hours, "enabled": t.enabled,
         "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None}
        for t in store.list_tasks(agent_id)
    ]
    detail["files"] = store.file_store.list_files(agent_id)
    return SafeJSONResponse(detail)


@app.get("/api/agents/{agent_id}/files/{filename}")
async def api_agent_file(agent_id: str, filename: str):
    store = ctx().store
    agent = store.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    content = store.file_store.read_file(agent_id, filename)
    if content is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    return SafeJSONResponse({"name": filename, "content": content})


def _agent_public(agent):
    return {
        "id": agent.id,
        "name": agent.name,
        "description": agent.description,
        "system_prompt": agent.system_prompt,
        "status": agent.status,
        "is_overseer": agent.is_overseer,
        "llm_provider": agent.llm_provider,
        "llm_api_key": _mask(agent.llm_api_key),
        "llm_base_url": agent.llm_base_url,
        "llm_model": agent.llm_model,
        "initial_capital": agent.initial_capital,
        "current_cash": agent.current_cash,
        "max_position": agent.max_position,
        "single_stock_weight": agent.single_stock_weight,
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
        "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
        "last_active_at": agent.last_active_at.isoformat() if agent.last_active_at else None,
    }


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return value[:4] + "****" + value[-4:]


@app.put("/api/agents/{agent_id}")
async def api_agent_update(agent_id: str, payload: dict):
    store = ctx().store
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent 不存在")
    fields = {}
    for key in ("name", "description", "system_prompt", "llm_provider",
                "llm_api_key", "llm_base_url", "llm_model", "status",
                "max_position", "single_stock_weight"):
        if key in payload:
            val = payload[key]
            if key == "llm_api_key" and isinstance(val, str) and "****" in val:
                continue  # 打码值不覆盖
            fields[key] = val
    if "status" in fields and fields["status"] not in ("running", "paused", "archived"):
        fields.pop("status")
    agent = store.update_agent(agent_id, **fields)
    ctx().task_scheduler.sync_all()
    return SafeJSONResponse({"agent": _agent_public(agent), "message": "Agent 已更新"})


@app.delete("/api/agents/{agent_id}")
async def api_agent_delete(agent_id: str):
    store = ctx().store
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent 不存在")
    ok = store.delete_agent(agent_id)
    ctx().task_scheduler.sync_all()
    if not ok:
        raise HTTPException(status_code=400, detail="统筹 Agent 不允许删除")
    return SafeJSONResponse({"deleted": True, "message": "Agent 已删除"})


@app.post("/api/agents/{agent_id}/run")
async def api_agent_run(agent_id: str):
    store = ctx().store
    agent = store.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    text = ctx().assistant.auto_run(agent)
    return SafeJSONResponse({"text": text})


# ---------------- Agent 数据查看 ----------------
@app.get("/api/agents/{agent_id}/portfolio")
async def api_agent_portfolio(agent_id: str):
    store = ctx().store
    agent = store.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    from src.agent.portfolio import AgentPortfolio

    return SafeJSONResponse(AgentPortfolio(agent, store).summary())


@app.get("/api/agents/{agent_id}/performance")
async def api_agent_performance(agent_id: str, limit: int = 120):
    store = ctx().store
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent 不存在")
    return SafeJSONResponse(store.list_performance(agent_id, limit=limit))


@app.get("/api/agents/{agent_id}/trades")
async def api_agent_trades(agent_id: str, limit: int = 100):
    store = ctx().store
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent 不存在")
    return SafeJSONResponse(store.list_trades(agent_id, limit=limit))


@app.get("/api/agents/{agent_id}/memories")
async def api_agent_memories(agent_id: str):
    store = ctx().store
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent 不存在")
    return SafeJSONResponse(
        [
            {"id": m.id, "content": m.content, "memory_type": m.memory_type,
             "created_at": m.created_at.isoformat() if m.created_at else None}
            for m in store.list_memories(agent_id)
        ]
    )


@app.post("/api/agents/{agent_id}/memories")
async def api_agent_memories_add(agent_id: str, payload: dict):
    store = ctx().store
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent 不存在")
    content = str(payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content 不能为空")
    item = store.add_memory(agent_id, content, memory_type=str(payload.get("memory_type") or "instruction"))
    return SafeJSONResponse({"id": item.id, "content": item.content})


# ---------------- Agent 对话（SSE 流式） ----------------
@app.get("/api/agents/{agent_id}/chat")
async def api_agent_chat_history(agent_id: str):
    store = ctx().store
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent 不存在")
    return SafeJSONResponse(store.list_chat(agent_id, limit=100))


@app.post("/api/agents/{agent_id}/chat")
async def api_agent_chat(agent_id: str, payload: dict):
    store = ctx().store
    agent = store.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    message = str(payload.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message 不能为空")

    def gen():
        yield _sse({"type": "start", "agent": agent.name})
        for event in ctx().assistant.run_stream(agent, message):
            yield _sse(event)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------- Agent 定时任务 ----------------
@app.post("/api/agents/{agent_id}/tasks")
async def api_agent_task_add(agent_id: str, payload: dict):
    store = ctx().store
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent 不存在")
    schedule_type = payload.get("schedule_type") or "daily"
    task = store.add_task(
        agent_id,
        schedule_type=schedule_type,
        schedule_time=str(payload.get("schedule_time") or "09:30"),
        interval_hours=float(payload.get("interval_hours") or 0),
        enabled=bool(payload.get("enabled", True)),
    )
    ctx().task_scheduler.sync_all()
    return SafeJSONResponse({"id": task.id, "message": "定时任务已创建"})


@app.put("/api/agents/{agent_id}/tasks/{task_id}")
async def api_agent_task_update(agent_id: str, task_id: int, payload: dict):
    store = ctx().store
    task = store.get_task(task_id)
    if not task or task.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="任务不存在")
    fields = {k: v for k, v in payload.items()
              if k in ("schedule_type", "schedule_time", "interval_hours", "enabled")}
    store.update_task(task_id, **fields)
    ctx().task_scheduler.sync_all()
    return SafeJSONResponse({"message": "定时任务已更新"})


@app.delete("/api/agents/{agent_id}/tasks/{task_id}")
async def api_agent_task_delete(agent_id: str, task_id: int):
    store = ctx().store
    task = store.get_task(task_id)
    if not task or task.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="任务不存在")
    store.delete_task(task_id)
    ctx().task_scheduler.sync_all()
    return SafeJSONResponse({"message": "定时任务已删除"})


# ---------------- 系统 ----------------
@app.get("/api/health")
async def api_health():
    health = ctx().metrics.health_check()
    health["system"] = ctx().metrics.collect_system_metrics().to_dict()
    return SafeJSONResponse(health)


@app.get("/api/llm/providers")
async def api_llm_providers():
    return SafeJSONResponse(
        {
            "default_provider": config.llm.default_provider,
            "generator_using": "agent",
        }
    )


@app.get("/api/llm/remote-models")
async def api_llm_remote_models(provider: str = "deepseek"):
    from src.core.config_store import MODEL_OPTIONS, fetch_remote_models

    models, error = fetch_remote_models(provider)
    return SafeJSONResponse(
        {
            "provider": provider,
            "models": models,
            "preset": MODEL_OPTIONS.get(provider, []),
            "error": error,
        }
    )


# ---------------- 系统配置 ----------------
@app.get("/api/config")
async def api_config():
    from src.core.config_store import ConfigStore

    store = ConfigStore()
    return SafeJSONResponse(
        {
            "schema": store.describe_all(),
            "categories": ["llm", "system", "data"],
            "category_names": {
                "llm": "大模型配置",
                "system": "系统运行时间",
                "data": "数据源",
            },
        }
    )


@app.put("/api/config")
async def api_config_save(payload: dict):
    from src.core.config_store import ConfigStore

    updates = payload.get("updates", {})
    if not isinstance(updates, dict) or not updates:
        raise HTTPException(status_code=400, detail="updates 不能为空")
    cleaned = {}
    for key, value in updates.items():
        val = str(value).strip()
        if not val:
            continue
        if ("api_key" in key or "token" in key) and "****" in val:
            continue
        cleaned[key] = val
    n = ConfigStore().save(cleaned)
    return SafeJSONResponse({"saved": n, "message": f"已保存 {n} 项配置"})


def init_web_app(store, assistant, task_scheduler, metrics, alert):
    """填充全局上下文（供 main.py 调用）"""
    ac = AppContext.get()
    ac.store = store
    ac.assistant = assistant
    ac.task_scheduler = task_scheduler
    ac.metrics = metrics
    ac.alert = alert
