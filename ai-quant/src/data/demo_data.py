"""演示数据生成器

当数据库无真实行情数据时，生成一段合成行情用于演示系统完整链路
（选股→模拟交易→进化→面板展示）。用户配置 TUSHARE_TOKEN 后可回填真实数据覆盖。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import text

from src.core.database import get_db_session


# 演示股票池（真实 A 股代码）
DEMO_STOCKS = [
    ("600519.SH", "600519", "贵州茅台", "白酒"),
    ("000858.SZ", "000858", "五粮液", "白酒"),
    ("601318.SH", "601318", "中国平安", "保险"),
    ("600036.SH", "600036", "招商银行", "银行"),
    ("000001.SZ", "000001", "平安银行", "银行"),
    ("600030.SH", "600030", "中信证券", "证券"),
    ("300750.SZ", "300750", "宁德时代", "电池"),
    ("002594.SZ", "002594", "比亚迪", "汽车"),
    ("600276.SH", "600276", "恒瑞医药", "医药"),
    ("300760.SZ", "300760", "迈瑞医疗", "医疗器械"),
    ("601012.SH", "601012", "隆基绿能", "光伏"),
    ("002415.SZ", "002415", "海康威视", "安防"),
    ("000333.SZ", "000333", "美的集团", "家电"),
    ("600887.SH", "600887", "伊利股份", "食品"),
    ("601888.SH", "601888", "中国中免", "免税"),
    ("600900.SH", "600900", "长江电力", "电力"),
    ("601988.SH", "601988", "中国银行", "银行"),
    ("002475.SZ", "002475", "立讯精密", "电子"),
    ("688981.SH", "688981", "中芯国际", "半导体"),
    ("603288.SH", "603288", "海天味业", "调味品"),
    ("000651.SZ", "000651", "格力电器", "家电"),
    ("600000.SH", "600000", "浦发银行", "银行"),
    ("601899.SH", "601899", "紫金矿业", "有色"),
    ("600028.SH", "600028", "中国石化", "石油"),
    ("000002.SZ", "000002", "万科A", "地产"),
    ("600585.SH", "600585", "海螺水泥", "建材"),
    ("601166.SH", "601166", "兴业银行", "银行"),
    ("002714.SZ", "002714", "牧原股份", "养殖"),
    ("300059.SZ", "300059", "东方财富", "证券"),
    ("601668.SH", "601668", "中国建筑", "建筑"),
]


def _demo_trade_dates(n_days: int):
    """生成演示交易日序列：仅工作日，并排除 A 股法定休市日。

    演示数据用于无真实行情时的开箱即用，日期须与真实交易日一致，
    否则残留的"休市日"价格会污染模拟回放（价格量级错位导致资金虚增）。
    """
    # A 股法定休市日（仅工作日部分，周末由 bdate_range 天然排除）
    A_SHARE_HOLIDAYS = [
        # 2025
        "2025-01-01", "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31",
        "2025-02-03", "2025-02-04", "2025-04-04", "2025-05-01", "2025-05-02",
        "2025-05-05", "2025-06-02", "2025-10-01", "2025-10-02", "2025-10-03",
        "2025-10-06", "2025-10-07", "2025-10-08",
        # 2026
        "2026-01-01", "2026-01-02", "2026-02-16", "2026-02-17", "2026-02-18",
        "2026-02-19", "2026-02-20", "2026-04-06", "2026-05-01", "2026-05-04",
        "2026-05-05", "2026-06-19", "2026-10-01", "2026-10-02", "2026-10-05",
        "2026-10-06", "2026-10-07", "2026-10-08",
    ]
    holidays = pd.DatetimeIndex(pd.to_datetime(A_SHARE_HOLIDAYS))
    days = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n_days + len(holidays) + 10)
    return days[~days.isin(holidays)].tail(n_days)


def generate_demo_data(days: int = 180) -> int:
    """生成演示行情数据，返回插入的股票数。daily_price 已有数据时跳过。"""
    from src.core.database import engine

    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM daily_price")).scalar()
    if count and count > 0:
        logger.info(f"已存在 {count} 条行情数据，跳过演示数据生成")
        return 0

    np.random.seed(42)
    n_stocks = len(DEMO_STOCKS)
    n_days = days
    trade_dates = _demo_trade_dates(n_days)

    # 生成行业差异化漂移和随机波动
    industries = [s[3] for s in DEMO_STOCKS]
    drift_map = {
        "白酒": 0.0008, "保险": 0.0002, "银行": 0.0003, "证券": 0.0006,
        "电池": 0.0010, "汽车": 0.0009, "医药": 0.0006, "光伏": 0.0007,
        "半导体": 0.0012, "家电": 0.0005, "食品": 0.0004, "免税": 0.0006,
        "电力": 0.0002, "电子": 0.0007, "调味品": 0.0004, "有色": 0.0006,
        "石油": 0.0001, "地产": -0.0001, "建材": 0.0002, "养殖": 0.0003,
        "建筑": 0.0001, "医疗器械": 0.0006, "安防": 0.0005,
    }
    base_price = {s[0]: np.random.uniform(15, 200) for s in DEMO_STOCKS}

    # 先清空旧演示数据（防止重复）
    with get_db_session() as session:
        session.execute(text("DELETE FROM daily_price"))
        session.execute(text("DELETE FROM stock_basic"))
        session.execute(text("DELETE FROM factor_data"))
        session.execute(text("DELETE FROM strategies"))
        session.execute(text("DELETE FROM strategy_performance"))
        session.execute(text("DELETE FROM evolution_log"))

    with get_db_session() as session:
        # 写入股票基础信息
        for ts_code, symbol, name, industry in DEMO_STOCKS:
            session.execute(
                text(
                    """
                    INSERT INTO stock_basic (ts_code, symbol, name, industry, market, list_date)
                    VALUES (:ts, :sym, :name, :ind, :mkt, :ld)
                    """
                ),
                {
                    "ts": ts_code,
                    "sym": symbol,
                    "name": name,
                    "ind": industry,
                    "mkt": "SH" if ts_code.endswith("SH") else "SZ",
                    "ld": "2010-01-01",
                },
            )
        # 写入合成行情
        rows = []
        for i, (ts_code, _, _, industry) in enumerate(DEMO_STOCKS):
            drift = drift_map.get(industry, 0.0004)
            vol = np.random.uniform(0.015, 0.035)
            rets = np.random.normal(drift, vol, n_days)
            close = base_price[ts_code] * np.cumprod(1 + rets)
            close = np.clip(close, 3, 800)
            open_ = close * (1 + np.random.normal(0, 0.005, n_days))
            high = np.maximum(open_, close) * (1 + np.abs(np.random.normal(0, 0.004, n_days)))
            low = np.minimum(open_, close) * (1 - np.abs(np.random.normal(0, 0.004, n_days)))
            pre_close = np.concatenate([[close[0]], close[:-1]])
            pct = close / pre_close - 1
            base_vol = np.random.uniform(5e5, 2e7)
            vol = base_vol * (1 + 0.3 * np.random.rand(n_days))
            amount = vol * close * 100
            turnover = np.random.uniform(0.5, 6, n_days)
            pe_ttm = np.random.uniform(8, 45, n_days)
            pb = np.random.uniform(0.8, 6, n_days)
            dv_ttm = np.random.uniform(0.5, 5, n_days)
            for j in range(n_days):
                rows.append(
                    {
                        "ts_code": ts_code,
                        "trade_date": trade_dates[j].date(),
                        "open": round(float(open_[j]), 3),
                        "high": round(float(high[j]), 3),
                        "low": round(float(low[j]), 3),
                        "close": round(float(close[j]), 3),
                        "pre_close": round(float(pre_close[j]), 3),
                        "change_pct": round(float(pct[j] * 100), 4),
                        "vol": round(float(vol[j]), 2),
                        "amount": round(float(amount[j]), 2),
                        "turnover_rate": round(float(turnover[j]), 4),
                        "pe_ttm": round(float(pe_ttm[j]), 3),
                        "pb": round(float(pb[j]), 3),
                        "dv_ttm": round(float(dv_ttm[j]), 3),
                    }
                )
        for i in range(0, len(rows), 5000):
            chunk = rows[i : i + 5000]
            session.execute(
                text(
                    """
                    INSERT INTO daily_price
                        (ts_code, trade_date, open, high, low, close,
                         pre_close, change_pct, vol, amount, turnover_rate,
                         pe_ttm, pb, dv_ttm)
                    VALUES
                        (:ts_code, :trade_date, :open, :high, :low, :close,
                         :pre_close, :change_pct, :vol, :amount, :turnover_rate,
                         :pe_ttm, :pb, :dv_ttm)
                    """
                ),
                chunk,
            )

    logger.info(f"演示行情数据生成完成: {n_stocks} 只股票 × {n_days} 个交易日")
    return n_stocks


def ensure_demo_data() -> bool:
    """确保系统有数据可用：daily_price 为空时生成演示数据"""
    from src.core.database import engine

    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM daily_price")).scalar()
    if count:
        return True
    return generate_demo_data() > 0
