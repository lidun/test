"""Agent 工具集：供 LLM 调用（查询股票、选股、模拟交易、记忆、排名）

每个工具函数签名统一为 (portfolio, store, args) -> str，返回值是给 LLM 的文本结果。
"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import text

from src.agent.models import Agent
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
    {
        "type": "function",
        "function": {
            "name": "summarize_all",
            "description": "（统筹 Agent）汇总所有交易 Agent 的收益、持仓与风格，用于全局把控与对比",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_agent_detail",
            "description": "查看指定 Agent 的详情（持仓、记忆、最近成交），便于统筹/借鉴其他 Agent 的做法",
            "parameters": {
                "type": "object",
                "properties": {"agent_id": {"type": "string", "description": "Agent 的 id"}},
                "required": ["agent_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": "把研究成果、策略笔记或分析报告保存到自己的文件区（data/agents/目录），长期留存",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "文件名，如 茅台分析.md"},
                    "content": {"type": "string", "description": "文件内容"},
                },
                "required": ["filename", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出自己文件区保存的笔记/报告文件",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取自己文件区里某个笔记/报告文件的内容",
            "parameters": {
                "type": "object",
                "properties": {"filename": {"type": "string", "description": "文件名"}},
                "required": ["filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_skillhub",
            "description": "在腾讯 SkillHub 技能市场搜索可用技能（如 PDF 处理、数据抓取、报告生成等），返回匹配技能的 slug、用途、下载量",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键词，如 周报、PDF、数据分析"},
                    "limit": {"type": "integer", "description": "返回条数，默认 5"},
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_skill",
            "description": "从腾讯 SkillHub 下载并安装技能到自己的技能目录（skills/），返回该技能的 SKILL.md 使用说明；安装后按说明执行任务",
            "parameters": {
                "type": "object",
                "properties": {"slug": {"type": "string", "description": "技能标识（search_skillhub 返回的 slug）"}},
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_skill",
            "description": "读取自己已安装技能（skills/ 目录）的 SKILL.md 使用说明",
            "parameters": {
                "type": "object",
                "properties": {"slug": {"type": "string", "description": "技能标识（安装时的 slug）"}},
                "required": ["slug"],
            },
        },
    },
]

# 不配置原生 function calling 时使用的文本协议说明（嵌入 system prompt）
TEXT_PROTOCOL_INSTRUCTION = """你可以通过输出 JSON 动作调用工具完成操作。当你需要查询行情、买卖股票或查看账户时，
在回复末尾（或单独一行）输出一个 JSON 块：
{"action": "工具名", "args": {"参数": "值"}}
可用动作：get_stock_quote, search_stocks, get_stock_history, get_market_overview,
get_factor_snapshot, get_portfolio, buy_stock, sell_stock, remember, recall_memory, get_ranking,
summarize_all, get_agent_detail, save_note, list_files, read_file,
search_skillhub, install_skill, read_skill。
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


def summarize_all(portfolio, store: AgentStore, args) -> str:
    """统筹 Agent：汇总全部 Agent 表现（收益、持仓、现金、风格）"""
    ranks = store.rank_agents(limit=100)
    if not ranks:
        return "暂无 Agent 数据"
    lines = ["【全局汇总】"]
    for i, r in enumerate(ranks, start=1):
        ret = r["cumulative_return"]
        ret_s = f"{ret*100:+.2f}%" if ret is not None else "暂无绩效"
        lines.append(
            f"#{i} {r['name']}: {ret_s} | 总资产 {_num(r['total_value'])} | "
            f"持仓 {r['positions_count'] or 0} 只 | 状态 {r['status']}"
        )
    return "\n".join(lines)


def get_agent_detail(portfolio, store: AgentStore, args) -> str:
    """统筹/借鉴：查看指定 Agent 的持仓、记忆与最近成交"""
    agent_id = str(args.get("agent_id") or "").strip()
    if not agent_id:
        return "请提供 agent_id"
    agent = store.get_agent(agent_id)
    if not agent:
        return f"未找到 Agent {agent_id}"
    from src.agent.portfolio import AgentPortfolio

    p = AgentPortfolio(agent, store)
    summary = p.summary()
    mems = store.list_memories(agent.id, limit=5)
    trades = store.list_trades(agent.id, limit=5)
    lines = [
        f"Agent「{agent.name}」({agent.id})",
        f"定位: {agent.description or '-'} | 状态 {agent.status}",
        f"账户: 现金 {summary['cash']:.2f} | 总资产 {summary['total_value']:.2f} | "
        f"累计收益 {summary['cumulative_return']*100:+.2f}% | 持仓 {summary['positions_count']} 只",
    ]
    if summary["positions"]:
        lines.append("持仓:")
        for ps in summary["positions"]:
            lines.append(
                f"  - {ps['name']}({ps['ts_code']}) {ps['shares']}股 "
                f"盈亏 {ps['pnl_pct']*100:+.2f}%"
            )
    if mems:
        lines.append("记忆:")
        lines += [f"  - {m.content[:120]}" for m in mems]
    if trades:
        lines.append("最近成交:")
        lines += [
            f"  - {t['trade_date']} {t['direction']} {t['ts_code']} "
            f"{t['shares']}股 @{t['price']} ({t['reason'][:60]})"
            for t in trades
        ]
    return "\n".join(lines)


def save_note(portfolio, store: AgentStore, args) -> str:
    filename = str(args.get("filename") or "").strip()
    content = str(args.get("content") or "").strip()
    if not content:
        return "文件内容为空"
    p = store.file_store.save_file(portfolio.agent.id, filename, content)
    return f"已保存文件: {p.name}（{len(content)} 字符）"


def list_files(portfolio, store: AgentStore, args) -> str:
    files = store.file_store.list_files(portfolio.agent.id)
    if not files:
        return "文件区暂无文件"
    return "\n".join(
        f"- {f['name']}（{f['size']} 字节）" for f in files
    )


def read_file(portfolio, store: AgentStore, args) -> str:
    filename = str(args.get("filename") or "").strip()
    if not filename:
        return "请提供文件名"
    content = store.file_store.read_file(portfolio.agent.id, filename)
    if content is None:
        return f"文件 {filename} 不存在"
    return content[:3000]


def search_skillhub(portfolio, store: AgentStore, args) -> str:
    from src.agent import skillhub

    keyword = str(args.get("keyword") or "").strip()
    limit = int(args.get("limit") or 5)
    try:
        items = skillhub.search_skills(keyword, limit=limit)
    except skillhub.SkillHubError as e:
        return f"SkillHub 搜索失败：{e}"
    if not items:
        return f"SkillHub 上未找到与「{keyword}」相关的技能，可换关键词重试"
    lines = [f"SkillHub 搜索「{keyword}」共 {len(items)} 个结果："]
    for i, it in enumerate(items, start=1):
        lines.append(
            f"{i}. {it['name']}（slug: {it['slug']}）\n"
            f"   用途: {it['description'][:120] or '-'}\n"
            f"   分类: {it['category']} | 下载 {it['downloads']} | "
            f"需API密钥: {it['requires_api_key']}"
        )
    lines.append("可用 install_skill 安装某个技能（参数 slug）后按其 SKILL.md 说明执行。")
    return "\n".join(lines)


def install_skill(portfolio, store: AgentStore, args) -> str:
    from src.agent import skillhub

    slug = str(args.get("slug") or "").strip()
    if not slug:
        return "请提供技能 slug"
    skills_dir = store.file_store.skills_dir(portfolio.agent.id)
    try:
        result = skillhub.install_skill(slug, skills_dir)
    except skillhub.SkillHubError as e:
        return f"安装技能失败：{e}"
    md = result.get("skill_md") or ""
    summary = skillhub.skill_summary(md, limit=2500)
    parts = [
        f"技能「{result['slug']}」已安装到 {result['dir']}",
        f"包含文件: {', '.join(result['files']) or '无'}",
    ]
    if summary:
        parts.append("SKILL.md 使用说明：\n" + summary)
    else:
        parts.append("该技能未提供 SKILL.md 说明。")
    return "\n\n".join(parts)


def read_skill(portfolio, store: AgentStore, args) -> str:
    from src.agent import skillhub

    slug = str(args.get("slug") or "").strip()
    if not slug:
        return "请提供技能 slug"
    skills_dir = store.file_store.skills_dir(portfolio.agent.id)
    md = skillhub.read_skill_md(skills_dir, slug, limit_chars=4000)
    if not md:
        return f"尚未安装技能 {slug}，先用 search_skillhub 搜索、install_skill 安装"
    return skillhub.skill_summary(md, limit=3500) or f"技能 {slug} 无 SKILL.md 内容"


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
    "summarize_all": summarize_all,
    "get_agent_detail": get_agent_detail,
    "save_note": save_note,
    "list_files": list_files,
    "read_file": read_file,
    "search_skillhub": search_skillhub,
    "install_skill": install_skill,
    "read_skill": read_skill,
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
