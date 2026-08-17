"""Agent 工具集：供 LLM 调用（查询股票、选股、模拟交易、记忆、排名）

每个工具函数签名统一为 (portfolio, store, args) -> str，返回值是给 LLM 的文本结果。
"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import text

from src.agent.models import Agent, MemoryItem
from src.agent.portfolio import AgentPortfolio
from src.agent.store import AgentStore
from src.core.database import get_db_session


def _num(v, nd=2):
    if v is None:
        return "-"
    return round(float(v), nd)


# ---------------- 工具定义（OpenAI function calling schema） ----------------

TOOLS_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_stock_quote",
            "description": "查询单只股票的实时/最新行情与估值（价格、涨跌幅、PE、PB、换手率）",
            "parameters": {
                "type": "object",
                "properties": {"symbol": {"type": "string", "description": "股票代码或代码.市场，如 600519 或 600519.SH"}},
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_stocks",
            "description": "按名称或代码关键字搜索股票，返回匹配的股票代码与名称",
            "parameters": {
                "type": "object",
                "properties": {"keyword": {"type": "string", "description": "股票名称或代码关键字，如 茅台"}},
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_history",
            "description": "查询某只股票最近 N 个交易日的历史行情（日期、收盘价、涨跌幅、成交量），用于技术分析",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码"},
                    "days": {"type": "integer", "description": "天数，默认30，最大120"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_overview",
            "description": "查询当前市场概况：上证指数表现、当日上涨/下跌/平盘股票家数，用于判断市场环境",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_factor_snapshot",
            "description": "查询最新一期因子排行榜，用于按因子选股（如 PE、PB、换手率、动量等）。返回因子值最高的股票。",
            "parameters": {
                "type": "object",
                "properties": {
                    "factor_name": {"type": "string", "description": "因子名，如 pe_ttm / pb / turnover_rate / change_pct / amount"},
                    "top_n": {"type": "integer", "description": "返回数量，默认10"},
                },
                "required": ["factor_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_portfolio",
            "description": "查看自己的模拟账户：现金、持仓明细、每只股票盈亏、总资产与累计收益率",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buy_stock",
            "description": "以最新收盘价模拟买入股票（含手续费与滑点），买入前请先用 get_stock_quote 确认价格与估值",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码"},
                    "shares": {"type": "integer", "description": "买入股数（按100股整数倍）"},
                    "reason": {"type": "string", "description": "买入理由"},
                },
                "required": ["symbol", "shares", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sell_stock",
            "description": "模拟卖出股票（含手续费与滑点）。shares 省略时全部卖出",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码"},
                    "shares": {"type": "integer", "description": "卖出股数，省略则全部卖出"},
                    "reason": {"type": "string", "description": "卖出理由"},
                },
                "required": ["symbol", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "把用户给你的策略规则、选股偏好或重要信息写入长期记忆，以后每次对话和自动任务都能回忆起来",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string", "description": "要长期记住的内容"}},
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_memory",
            "description": "回忆自己长期记忆中的策略规则与偏好（可按关键字过滤）",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string", "description": "要回忆的主题关键字，可为空"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ranking",
            "description": "查询所有交易 Agent 的累计收益排名，以及自己的名次",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# 不配置原生 function calling 时使用的文本协议说明（嵌入 system prompt）
TEXT_PROTOCOL_INSTRUCTION = """你可以通过输出 JSON 动作调用工具完成操作。当你需要查询行情、买卖股票或查看账户时，
在回复末尾（或单独一行）输出一个 JSON 块：
{"action": "工具名", "args": {"参数": "值"}}
可用动作：get_stock_quote, search_stocks, get_stock_history, get_market_overview,
get_factor_snapshot, get_portfolio, buy_stock, sell_stock, remember, recall_memory, get_ranking。
一次只输出一个动作，执行结果会在下一轮告诉你。
"""


# ---------------- 工具实现 ----------------

def get_stock_quote(portfolio, store, args) -> str:
    symbol = str(args.get("symbol", "")).strip()
    if not symbol:
        return "请提供股票代码"
    code = _normalize_symbol(symbol)
    with get_db_session() as session:
        row = session.execute(
            text(
                "SELECT d.ts_code, b.name, d.close, d.change_pct, d.turnover_rate, "
                "d.pe_ttm, d.pb, d.dv_ttm, d.trade_date, d.amount "
                "FROM daily_price d LEFT JOIN stock_basic b ON b.ts_code = d.ts_code "
                "WHERE d.ts_code = :code ORDER BY d.trade_date DESC LIMIT 1"
            ),
            {"code": code},
        ).fetchone()
    if not row:
        return f"未找到 {symbol} 的行情数据"
    return (
        f"{row[1] or code} ({row[0]}) 最新交易日 {row[8]}:\n"
        f"收盘价 {_num(row[2], 3)}，涨跌幅 {_num(row[3])}%，"
        f"换手率 {_num(row[4])}%，成交额 {_num(float(row[9])/1e8, 2)}亿元\n"
        f"PE(TTM) {_num(row[5])}，PB {_num(row[6])}，股息率 {_num(row[7])}%"
    )


def search_stocks(portfolio, store, args) -> str:
    keyword = str(args.get("keyword", "")).strip()
    if not keyword:
        return "请提供搜索关键字"
    with get_db_session() as session:
        rows = session.execute(
            text(
                "SELECT ts_code, name, industry FROM stock_basic "
                "WHERE name ILIKE :kw OR ts_code ILIKE :kw OR symbol ILIKE :kw "
                "ORDER BY ts_code LIMIT 20"
            ),
            {"kw": f"%{keyword}%"},
        ).fetchall()
    if not rows:
        return f"未找到匹配「{keyword}」的股票"
    return "\n".join(f"{r[1]}({r[0]}) 行业:{r[2] or '-'}" for r in rows)


def get_stock_history(portfolio, store, args) -> str:
    symbol = str(args.get("symbol", "")).strip()
    days = min(int(args.get("days") or 30), 120)
    code = _normalize_symbol(symbol)
    with get_db_session() as session:
        rows = session.execute(
            text(
                "SELECT trade_date, close, change_pct, vol FROM daily_price "
                "WHERE ts_code = :code ORDER BY trade_date DESC LIMIT :days"
            ),
            {"code": code, "days": days},
        ).fetchall()
    rows = rows[::-1]
    if not rows:
        return f"未找到 {symbol} 的历史行情"
    lines = [f"{symbol} 最近 {len(rows)} 个交易日 (日期 收盘 涨跌幅% 成交量):"]
    for r in rows:
        lines.append(f"{r[0]} {_num(r[1], 3)} {_num(r[2])}% {_num(float(r[3])/1e4, 1)}万手")
    return "\n".join(lines)


def get_market_overview(portfolio, store, args) -> str:
    with get_db_session() as session:
        idx = session.execute(
            text(
                "SELECT close, change_pct FROM daily_price WHERE ts_code = '000001.SH' "
                "ORDER BY trade_date DESC LIMIT 1"
            ),
        ).fetchone()
        stats = session.execute(
            text(
                "SELECT "
                "  COUNT(*) FILTER (WHERE change_pct > 0) AS up, "
                "  COUNT(*) FILTER (WHERE change_pct < 0) AS down, "
                "  COUNT(*) FILTER (WHERE change_pct = 0) AS flat "
                "FROM daily_price WHERE trade_date = (SELECT MAX(trade_date) FROM daily_price)"
            ),
        ).fetchone()
    lines = []
    if idx:
        lines.append(f"上证指数: {_num(idx[0], 2)} ({_num(idx[1])}%)")
    if stats:
        lines.append(f"当日全市场: 上涨 {stats[0]} 家, 下跌 {stats[1]} 家, 平盘 {stats[2]} 家")
    return "\n".join(lines) if lines else "暂无市场数据"


def get_factor_snapshot(portfolio, store, args) -> str:
    factor = str(args.get("factor_name", "")).strip()
    top_n = min(int(args.get("top_n") or 10), 30)
    # 列名白名单，防止任意 SQL 注入
    allowed = {
        "pe_ttm", "pb", "dv_ttm", "turnover_rate",
        "change_pct", "amount", "close", "open", "high", "low", "vol",
    }
    if factor not in allowed:
        return f"因子 {factor} 无效（可用: {', '.join(sorted(allowed))}）"
    with get_db_session() as session:
        rows = session.execute(
            text(
                f"""
                SELECT d.ts_code, b.name, d.close, d.{factor}
                FROM daily_price d LEFT JOIN stock_basic b ON b.ts_code = d.ts_code
                WHERE d.trade_date = (SELECT MAX(trade_date) FROM daily_price)
                  AND d.{factor} IS NOT NULL
                ORDER BY d.{factor} DESC LIMIT :n
                """
            ),
            {"n": top_n},
        ).fetchall()
    if not rows:
        return f"因子 {factor} 暂无数据"
    return "\n".join(f"{r[1] or r[0]}({r[0]}) {factor}={_num(r[3])}, 收盘 {_num(r[2], 3)}" for r in rows)


def get_portfolio(portfolio: AgentPortfolio, store, args) -> str:
    s = portfolio.summary()
    if not s["positions"]:
        return (
            f"当前账户: 现金 {s['cash']:.2f}，总资产 {s['total_value']:.2f}，"
            f"累计收益 {s['cumulative_return']*100:.2f}%，暂无持仓"
        )
    lines = [
        f"账户 {s['name']}: 现金 {s['cash']:.2f}，总资产 {s['total_value']:.2f}，"
        f"累计收益 {s['cumulative_return']*100:.2f}%"
    ]
    for p in s["positions"]:
        lines.append(
            f"持仓 {p['name']}({p['ts_code']}) {p['shares']}股 成本{p['avg_cost']} "
            f"现价{p['current_price']} 盈亏{p['pnl_pct']*100:.2f}%"
        )
    return "\n".join(lines)


def buy_stock(portfolio: AgentPortfolio, store, args) -> str:
    symbol = str(args.get("symbol", "")).strip()
    shares = int(args.get("shares") or 0)
    reason = str(args.get("reason", ""))[:200]
    code = _normalize_symbol(symbol)
    res = portfolio.buy(code, shares, reason=reason)
    return res["message"]


def sell_stock(portfolio: AgentPortfolio, store, args) -> str:
    symbol = str(args.get("symbol", "")).strip()
    shares = args.get("shares")
    reason = str(args.get("reason", ""))[:200]
    code = _normalize_symbol(symbol)
    res = portfolio.sell(code, shares=int(shares) if shares else None, reason=reason)
    return res["message"]


def remember(portfolio, store: AgentStore, args) -> str:
    content = str(args.get("content", "")).strip()
    if not content:
        return "没有可记录的内容"
    store.add_memory(portfolio.agent.id, content, memory_type="instruction")
    return f"已记住: {content}"


def recall_memory(portfolio, store: AgentStore, args) -> str:
    question = str(args.get("question") or "").strip()
    items = store.recall_memories(portfolio.agent.id, query=question, limit=10)
    if not items:
        return "目前还没有长期记忆内容"
    return "\n".join(f"- {i.content}" for i in items)


def get_ranking(portfolio, store: AgentStore, args) -> str:
    ranks = store.rank_agents(limit=50)
    if not ranks:
        return "暂无其他 Agent"
    lines = []
    for i, r in enumerate(ranks, start=1):
        marker = " ← 我" if r["agent_id"] == portfolio.agent.id else ""
        ret = r["cumulative_return"]
        lines.append(
            f"第{i}名 {r['name']} 收益 {ret*100:.2f}% 总资产 {_num(r['total_value'])}"
            f"{marker}" if ret is not None else f"第{i}名 {r['name']} 暂无绩效{marker}"
        )
    return "\n".join(lines)


# ---------------- 分发 ----------------

def dispatch(portfolio: AgentPortfolio, store: AgentStore, name: str, args: dict) -> str:
    func = _FUNCS.get(name)
    if func is None:
        return f"未知工具: {name}"
    try:
        return func(portfolio, store, args or {})
    except Exception as e:  # noqa: BLE001
        return f"工具 {name} 执行失败: {e}"


_FUNCS = {
    "get_stock_quote": get_stock_quote,
    "search_stocks": search_stocks,
    "get_stock_history": get_stock_history,
    "get_market_overview": get_market_overview,
    "get_factor_snapshot": get_factor_snapshot,
    "get_portfolio": get_portfolio,
    "buy_stock": buy_stock,
    "sell_stock": sell_stock,
    "remember": remember,
    "recall_memory": recall_memory,
    "get_ranking": get_ranking,
}


def _normalize_symbol(symbol: str) -> str:
    """把用户输入归一化为 ts_code 格式：600519 -> 600519.SH, 000001 -> 000001.SZ"""
    s = symbol.strip()
    if "." in s:
        return s.upper()
    if s.startswith(("6", "9")) and len(s) == 6:
        return s + ".SH"
    if s.startswith(("0", "3", "2")) and len(s) == 6:
        return s + ".SZ"
    if s.startswith(("4", "8")) and len(s) == 6:
        return s + ".BJ"
    # 若未匹配，仍尝试精确查询（stock_basic 有原始代码）
    return s
