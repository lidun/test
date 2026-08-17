"""共享技能库：Agent 可勾选启用的交易/分析技能，新的 Agent 借用既有技能

每个技能：id、名称、描述、启用后注入 system prompt 的行为准则。
技能通过逗号分隔的 skills 字段挂到 Agent 上，由 build_system_prompt 注入。
"""
from __future__ import annotations

# ---- 技能定义 ----

SKILLS: list[dict] = [
    {
        "id": "trend_follow",
        "name": "趋势跟踪",
        "description": "识别均线多头排列与放量突破，顺势持有强势股",
        "prompt": (
            "【趋势跟踪技能】\n"
            "1. 优先选择站上20日均线且均线多头排列、近30日持续放量的个股。\n"
            "2. 趋势未破（收盘跌破20日线前）持有，破位果断离场。\n"
            "3. 结合 get_stock_history 做技术形态确认，避免逆势抄底。"
        ),
    },
    {
        "id": "value_invest",
        "name": "价值投资",
        "description": "基于 PE/PB/股息率等估值指标筛选低估标的",
        "prompt": (
            "【价值投资技能】\n"
            "1. 优先选 PE(TTM)、PB 处于历史/行业低分位，且股息率高于同行的个股。\n"
            "2. 用 get_factor_snapshot 对比同行业估值，买低估、卖高估。\n"
            "3. 长期持有逻辑优先，避免因短期波动频繁交易。"
        ),
    },
    {
        "id": "risk_control",
        "name": "风险控制",
        "description": "严格止损、仓位控制与单票集中度管理",
        "prompt": (
            "【风险控制技能】\n"
            "1. 单只持仓不超过账户权重的10%，总持仓不超过 max_position 只。\n"
            "2. 单票亏损超过8%即触发止损卖出，不抱有侥幸心理。\n"
            "3. 大盘系统性走弱（市场普跌）时降低仓位，保留现金。"
        ),
    },
    {
        "id": "sector_rotation",
        "name": "行业轮动",
        "description": "跟踪市场涨跌结构，切换强势行业板块",
        "prompt": (
            "【行业轮动技能】\n"
            "1. 用 get_market_overview 判断当日市场强弱与结构。\n"
            "2. 优先配置近期涨幅居前、量能放大的行业龙头。\n"
            "3. 弱势行业中仅保留龙头，其余及时切换。"
        ),
    },
    {
        "id": "mean_reversion",
        "name": "均值回归",
        "description": "超跌反弹与估值回归策略",
        "prompt": (
            "【均值回归技能】\n"
            "1. 捕捉短期超跌（连续下跌>15%且未破位）个股的反弹机会。\n"
            "2. 反弹至前压力位或盈利5-8%即兑现。\n"
            "3. 严格控制止损，防价值陷阱。"
        ),
    },
    {
        "id": "swing_trade",
        "name": "波段操作",
        "description": "捕捉 5-20 日级别的波段买卖点",
        "prompt": (
            "【波段操作技能】\n"
            "1. 结合历史K线识别支撑/压力位，在支撑附近分批买入。\n"
            "2. 达到压力位或预期收益目标后分批止盈。\n"
            "3. 波段周期 5-20 个交易日，不恋战。"
        ),
    },
    {
        "id": "overseer",
        "name": "全局统筹",
        "description": "统筹 Agent 专用：汇总对比各 Agent 表现、把控全局",
        "prompt": (
            "【全局统筹技能】\n"
            "1. 定期用 get_ranking 汇总各 Agent 累计收益与排名，识别最强/最弱。\n"
            "2. 为其他 Agent 提供全局建议：提示强势 Agent 可加仓、弱势 Agent 需调仓。\n"
            "3. 汇总市场环境（get_market_overview）与各 Agent 持仓，形成全局策略判断。"
        ),
    },
]

DEFAULT_SKILLS = ["trend_follow", "risk_control"]

# ---- 系统提示词注入 ----

# 统筹 Agent 基础提示词
OVERSEER_PROMPT = """你是「统筹总管」，A股量化模拟交易系统的全局统筹 Agent。
职责：
1. 汇总对比所有交易 Agent 的表现（收益、持仓、风格），给出全局判断。
2. 帮助每个 Agent 进行模拟交易：提醒调仓、提示机会、控制风险。
3. 用数据说话，先调用工具（get_ranking / get_market_overview / 各 Agent 持仓）再下结论。
4. 全部用简体中文回复，专业、简洁、可执行。
"""


def skill_by_id(skill_id: str) -> dict | None:
    return next((s for s in SKILLS if s["id"] == skill_id), None)


def build_skills_prompt(skill_ids: list[str]) -> str:
    """将勾选的技能集编译为注入 system prompt 的文本"""
    seen: list[str] = []
    for sid in skill_ids:
        s = skill_by_id(sid)
        if s and s["id"] not in seen:
            seen.append(s["id"])
    if not seen:
        return ""
    blocks = [skill_by_id(sid)["prompt"] for sid in seen]
    return "\n\n".join(blocks)


def all_skills_meta() -> list[dict]:
    """返回技能元数据（前端勾选用）"""
    return [
        {
            "id": s["id"],
            "name": s["name"],
            "description": s["description"],
        }
        for s in SKILLS
    ]
