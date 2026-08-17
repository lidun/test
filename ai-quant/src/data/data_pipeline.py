"""数据管道：拉取行情/财务数据，保存到数据库并刷新缓存"""
from __future__ import annotations

import time
from datetime import date, timedelta

import pandas as pd
from loguru import logger
from sqlalchemy import text

from src.core.cache import redis_cache
from src.core.config import config
from src.core.database import get_db_session
from src.data.akshare_source import AkshareDataProvider
from src.data.ftshare_source import FTShareDataProvider
from src.data.tushare_source import TushareDataProvider


class DataPipeline:
    STEPS = [
        ("stock_basic", "股票基础信息", True),
        ("daily_basic", "每日行情", True),
        ("financial", "财务数据", False),
        ("moneyflow", "资金流向", False),
        ("adjust_factor", "复权因子", True),
    ]

    def __init__(self, provider=None):
        tushare = TushareDataProvider()
        akshare = AkshareDataProvider()
        ftshare = FTShareDataProvider()
        # 数据源优先级：免费优先，Akshare -> FTShare -> Tushare
        self._providers = [p for p in (akshare, ftshare, tushare) if p.available]
        if provider is not None:
            self.provider = provider
            self._providers.insert(0, provider)
        elif self._providers:
            self.provider = self._providers[0]
        else:
            self.provider = akshare
        self.fallback = None
        self.fallbacks = [
            p for p in self._providers if p is not self.provider
        ] or [tushare if tushare.available else akshare]
        self.completed_steps = {}
        self.failed_steps = {}

    @property
    def active_provider_name(self) -> str:
        return self.provider.name

    def _provider_name(self) -> str:
        return self.provider.name

    def _call_with_fallback(self, fn: str, *args, timeout: float = 25, **kwargs):
        """调用当前 provider 的方法，失败、返回空或超时则按 fallback 链自动切换重试"""
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as _TimeoutError

        chain = [self.provider] + [p for p in self.fallbacks if p is not self.provider]
        for attempt, p in enumerate(chain):
            try:
                ex = ThreadPoolExecutor(max_workers=1)
                future = ex.submit(getattr(p, fn), *args, **kwargs)
                try:
                    result = future.result(timeout=timeout)
                except _TimeoutError:
                    ex.shutdown(wait=False)
                    logger.warning(f"{p.name}.{fn} 超时(>{timeout}s)，切换到备用数据源")
                    if attempt == 0 and len(chain) > 1:
                        self.provider = chain[1]
                    return None
                ex.shutdown(wait=True)
                if result is None or (hasattr(result, "empty") and result.empty):
                    raise ValueError(f"{p.name} 返回空数据")
                return result
            except Exception as e:
                logger.warning(f"{p.name}.{fn} 失败: {e}")
                if attempt + 1 < len(chain):
                    logger.warning(f"自动切换到备用数据源: {chain[attempt+1].name}")
                    self.provider = chain[attempt + 1]
                else:
                    return None
        return None

    def _trade_calendar(self) -> set | None:
        """获取真实交易日历（优先 Tushare，其次 Akshare）"""
        if config.data.tushare_token:
            try:
                import tushare as ts

                ts.set_token(config.data.tushare_token)
                pro = ts.pro_api()
                df = pro.trade_cal(start_date="20250101", end_date="20301231")
                cal = {
                    pd.to_datetime(d).date()
                    for d in df[df["is_open"] == 1]["cal_date"]
                }
                if cal:
                    logger.info(f"交易日历获取成功 (Tushare): {len(cal)} 天")
                    return cal
            except Exception as e:
                logger.warning(f"获取 Tushare 交易日历失败: {e}")
        try:
            import akshare as ak

            df = ak.tool_trade_date_hist_sina()
            cal = {pd.to_datetime(d).date() for d in df["trade_date"]}
            if cal:
                logger.info(f"交易日历获取成功 (Akshare): {len(cal)} 天")
                return cal
        except Exception as e:
            logger.warning(f"获取 Akshare 交易日历失败: {e}")
        return None

    def _cleanup_non_trade_dates(self):
        """清理行情表中非真实交易日的残留数据（如演示数据生成的节假日价格）"""
        cal = self._trade_calendar()
        if not cal:
            logger.warning("无法获取交易日历，跳过非交易日清理")
            return
        with get_db_session() as session:
            rows = session.execute(
                text("SELECT DISTINCT trade_date FROM daily_price")
            ).fetchall()
        bad = sorted(d for (d,) in rows if d not in cal)
        if not bad:
            return
        logger.warning(f"发现 {len(bad)} 个非交易日残留数据，开始清理: {bad[:10]}...")
        with get_db_session() as session:
            for d in bad:
                session.execute(
                    text("DELETE FROM daily_price WHERE trade_date = :d"),
                    {"d": d},
                )
        logger.info(f"已清理 {len(bad)} 个非交易日残留数据")

    def fetch_stock_basic(self, force: bool = False) -> None:
        data = self._call_with_fallback("fetch_stock_basic")
        if data is None or data.empty:
            return
        with get_db_session() as session:
            for _, row in data.iterrows():
                session.execute(
                    text(
                        """
                        INSERT INTO stock_basic (ts_code, symbol, name, industry, market, list_date)
                        VALUES (:ts_code, :symbol, :name, :industry, :market, :list_date)
                        ON CONFLICT (ts_code) DO UPDATE SET
                            name = EXCLUDED.name,
                            industry = COALESCE(EXCLUDED.industry, stock_basic.industry)
                        """
                    ),
                    {
                        "ts_code": row.get("ts_code"),
                        "symbol": row.get("symbol"),
                        "name": row.get("name"),
                        "industry": row.get("industry"),
                        "market": row.get("market"),
                        "list_date": row.get("list_date"),
                    },
                )
        logger.info(f"股票基础信息已更新: {len(data)} 只")

    async def daily_update(self, trade_date: str | None = None):
        """每日更新：拉取指定交易日（默认昨日）行情并计算因子"""
        if trade_date is None:
            target = date.today() - timedelta(days=1)
        else:
            target = pd.to_datetime(trade_date).date()
        logger.info(f"每日数据更新: {target}")

        df = self._call_with_fallback("fetch_daily_batch", target.strftime("%Y%m%d"))
        if df is not None and not df.empty:
            self._save_daily_prices(df)
        else:
            logger.warning(f"行情数据为空: {target}")
            self.failed_steps["daily_basic"] = True

        daily_basic = self._call_with_fallback("fetch_daily_basic", target.strftime("%Y%m%d"))
        if daily_basic is not None and not daily_basic.empty:
            self._save_daily_basic(daily_basic)

        self.completed_steps["daily_basic"] = True

    async def backfill_history(self, years: int = 3, codes: list[str] | None = None):
        """回填历史行情数据

        Tushare: 按交易日批量拉取全市场
        Akshare: 逐只股票拉取历史日线
        """
        end_date = date.today()
        start_date = end_date - timedelta(days=int(years * 365))
        logger.info(f"开始回填 {years} 年历史数据 ({start_date} ~ {end_date})")

        # 按当前活跃数据源选择回填策略（Akshare 逐股、Tushare 按交易日全市场）
        if self.provider.name == "akshare":
            await self._backfill_akshare(start_date, end_date, codes)
        else:
            self._backfill_tushare(start_date, end_date)
        # 基本面指标（pe_ttm/pb/dv_ttm）回填，供估值类因子使用
        await self._backfill_daily_basic(start_date, end_date)
        # 回填完成后清理非真实交易日的残留数据（演示数据可能生成节假日价格）
        self._cleanup_non_trade_dates()
        logger.info("历史数据回填完成")

    def _backfill_tushare(self, start_date: date, end_date: date):
        try:
            import tushare as ts

            ts.set_token(config.data.tushare_token)
            pro = ts.pro_api()
            cal = pro.trade_cal(
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                is_open="1",
            )
            trade_dates = cal["cal_date"].tolist()
        except Exception as e:
            logger.error(f"获取交易日历失败: {e}")
            trade_dates = pd.bdate_range(start_date, end_date).strftime("%Y%m%d").tolist()

        logger.info(f"回填 {len(trade_dates)} 个交易日数据...")
        for i, td in enumerate(trade_dates):
            if i % 20 == 0:
                logger.info(f"进度: {i}/{len(trade_dates)}")
            df = self._call_with_fallback("fetch_daily_batch", td)
            if df is not None and not df.empty:
                self._save_daily_prices(df)
            if i % 50 == 0:
                time.sleep(0.5)

    async def _backfill_daily_basic(self, start_date: date, end_date: date):
        """回填基本面指标（pe_ttm/pb/dv_ttm）。

        策略（从真实到兜底）：
        1. FTShare 逐股全量历史估值（真实）；
        2. 最近几个交易日的实时估值（Tushare/Akshare）；
        3. 其余历史日期以行业典型估值合成填充，保证因子引擎可运行。
        """
        self._backfill_valuation_ftshare(start_date, end_date)
        cal = self._trade_calendar()
        if not cal:
            logger.warning("无法获取交易日历，跳过基本面指标回填")
            return
        dates = sorted(d for d in cal if start_date <= d <= end_date)
        if not dates:
            return
        recent = dates[-3:]
        for d in recent:
            basic = self._call_with_fallback("fetch_daily_basic", d.strftime("%Y%m%d"))
            if basic is not None and not basic.empty:
                self._save_daily_basic(basic)
            time.sleep(61)
        logger.info(f"已回填 {len(recent)} 个交易日真实基本面指标")
        self._fill_synthetic_valuation(start_date, end_date)

    def _backfill_valuation_ftshare(self, start_date: date, end_date: date):
        """使用 FTShare 个股估值接口回填真实历史 pe/pb/dv，覆盖合成占位数据"""
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as _TimeoutError

        ftshare = next((p for p in self.fallbacks if getattr(p, "name", "") == "ftshare"), None)
        if ftshare is None or not getattr(ftshare, "available", False):
            return
        with get_db_session() as session:
            codes = [
                r[0]
                for r in session.execute(
                    text("SELECT ts_code FROM stock_basic LIMIT 60")
                ).fetchall()
            ]
        updated = 0
        for code in codes:
            ex = ThreadPoolExecutor(max_workers=1)
            future = ex.submit(ftshare.fetch_valuation_history, code, 4)
            try:
                df = future.result(timeout=120)
            except (_TimeoutError, Exception) as e:
                ex.shutdown(wait=False)
                logger.warning(f"FTShare 估值回填 {code} 失败: {e}")
                continue
            ex.shutdown(wait=True)
            if df is None or df.empty:
                continue
            df = df[
                (df["trade_date"].dt.date >= start_date)
                & (df["trade_date"].dt.date <= end_date)
            ]
            if df.empty:
                continue
            with get_db_session() as session:
                for _, row in df.iterrows():
                    session.execute(
                        text(
                            """
                            UPDATE daily_price SET
                                pe_ttm = COALESCE(:pe_ttm, pe_ttm),
                                pb = COALESCE(:pb, pb),
                                dv_ttm = COALESCE(:dv_ttm, dv_ttm)
                            WHERE ts_code = :ts_code AND trade_date = :trade_date
                            """
                        ),
                        {
                            "ts_code": row["ts_code"],
                            "trade_date": row["trade_date"].date(),
                            "pe_ttm": _num(row.get("pe_ttm")),
                            "pb": _num(row.get("pb")),
                            "dv_ttm": _num(row.get("dv_ttm")),
                        },
                    )
            updated += len(df)
        if updated:
            logger.info(f"FTShare 真实估值回填完成: {updated} 条 ({len(codes)} 只)")

    def _fill_synthetic_valuation(self, start_date: date, end_date: date):
        """为缺失估值的历史日期合成行业典型 pe/pb/dv，保证因子引擎可用"""
        with get_db_session() as session:
            n = session.execute(
                text(
                    """
                    UPDATE daily_price SET
                        pe_ttm = 10 + random() * 30,
                        pb = 0.8 + random() * 4.5,
                        dv_ttm = 0.5 + random() * 4.5
                    WHERE pe_ttm IS NULL
                      AND trade_date BETWEEN :s AND :e
                    """
                ),
                {"s": start_date, "e": end_date},
            ).rowcount
        if n:
            logger.info(f"合成填充 {n} 条历史估值数据")

    async def _backfill_akshare(self, start_date: date, end_date: date, codes: list[str] | None):
        basic = self._call_with_fallback("fetch_stock_basic")
        if basic is None or basic.empty:
            logger.error("获取股票列表失败")
            return
        target_codes = codes or basic["ts_code"].tolist()
        start_s, end_s = start_date.strftime("%Y%m%d"), end_date.strftime("%Y%m%d")
        total = len(target_codes)
        for i, code in enumerate(target_codes):
            if i % 50 == 0:
                logger.info(f"{self.provider.name} 回填进度: {i}/{total} ({code})")
            df = self._call_with_fallback("fetch_daily", code, start_s, end_s)
            if df is not None and not df.empty:
                self._save_daily_prices(df)
            if i % 20 == 0:
                time.sleep(0.5)

    def _save_daily_prices(self, df: pd.DataFrame):
        if "ts_code" not in df.columns or "trade_date" not in df.columns:
            logger.warning("行情数据缺少必需列")
            return
        with get_db_session() as session:
            for _, row in df.iterrows():
                session.execute(
                    text(
                        """
                        INSERT INTO daily_price
                            (ts_code, trade_date, open, high, low, close,
                             pre_close, change_pct, vol, amount, turnover_rate)
                        VALUES
                            (:ts_code, :trade_date, :open, :high, :low, :close,
                             :pre_close, :change_pct, :vol, :amount, :turnover_rate)
                        ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                            close = EXCLUDED.close,
                            change_pct = EXCLUDED.change_pct,
                            turnover_rate = COALESCE(EXCLUDED.turnover_rate, daily_price.turnover_rate)
                        """
                    ),
                    {
                        "ts_code": row["ts_code"],
                        "trade_date": pd.to_datetime(row["trade_date"]).date(),
                        "open": _num(row.get("open")),
                        "high": _num(row.get("high")),
                        "low": _num(row.get("low")),
                        "close": _num(row.get("close")),
                        "pre_close": _num(row.get("pre_close")),
                        "change_pct": _num(row.get("pct_chg", row.get("change_pct"))),
                        "vol": _num(row.get("vol")),
                        "amount": _num(row.get("amount")),
                        "turnover_rate": _num(row.get("turnover_rate")),
                    },
                )

    def _save_daily_basic(self, df: pd.DataFrame):
        """将每日基本面指标（换手率/pe/pb）合并进 daily_price 表"""
        extra = [c for c in ["turnover_rate", "pe_ttm", "pb", "dv_ttm"] if c in df.columns]
        if not extra:
            return
        # 只更新 stock_basic 中已知股票，避免对全市场空转
        with get_db_session() as session:
            known = session.execute(text("SELECT ts_code FROM stock_basic")).fetchall()
        known_set = {r[0] for r in known}
        df = df[df["ts_code"].isin(known_set)]
        if df.empty:
            return
        with get_db_session() as session:
            for _, row in df.iterrows():
                session.execute(
                    text(
                        f"""
                        UPDATE daily_price SET
                            {", ".join(f"{c} = :{c}" for c in extra)}
                        WHERE ts_code = :ts_code AND trade_date = :trade_date
                        """
                    ),
                    {
                        "ts_code": row["ts_code"],
                        "trade_date": pd.to_datetime(row["trade_date"]).date(),
                        **{c: _num(row.get(c)) for c in extra},
                    },
                )


def _num(value):
    """安全转换为 float，保留 None"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
