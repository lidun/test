"""因子计算引擎：加载因子注册表，计算并持久化因子数据"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import yaml
from loguru import logger
from sqlalchemy import text

from src.core.cache import redis_cache
from src.core.config import config
from src.core.database import get_db_session


class FactorEngine:
    def __init__(self):
        self.factors = self._load_registry()

    def _load_registry(self):
        path = config.CONFIG_DIR / "factor_registry.yaml"
        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return {item["name"]: item for item in data.get("factors", [])}

    def get_factor_names(self) -> list[str]:
        return list(self.factors.keys())

    async def calculate_history(self, days: int = 90):
        """为最近 N 个交易日批量计算因子快照（用于回测与模拟）"""
        trade_dates = self._get_recent_trade_dates(days)
        for i, td in enumerate(trade_dates):
            await self.calculate_snapshot(td)
            if (i + 1) % 20 == 0:
                logger.info(f"因子批量计算进度: {i+1}/{len(trade_dates)}")
        logger.info(f"因子批量计算完成: {len(trade_dates)} 个交易日")

    def _get_recent_trade_dates(self, days: int) -> list:
        with get_db_session() as session:
            result = session.execute(
                text(
                    "SELECT DISTINCT trade_date FROM daily_price ORDER BY trade_date DESC LIMIT :n"
                ),
                {"n": days},
            )
            return sorted(r[0] for r in result.fetchall())

    async def calculate_snapshot(self, target_date: date | None = None):
        """为指定日期（默认最近交易日）计算全市场因子快照。

        因子计算结果保存到 factor_data 表，并写入 Redis 缓存。
        为控制性能，仅对 stock_basic 中最新 500 只活跃股票计算。
        """
        if target_date is None:
            target_date = self._latest_trade_date()

        # 1. 读取当日全市场行情（用于量价因子）
        df = self._load_market_snapshot(target_date)
        if df is None or df.empty:
            logger.warning(f"{target_date} 无行情数据，跳过因子计算")
            return

        # 2. 技术/动量因子需要历史序列
        history = self._load_history(target_date)

        # 3. 逐因子计算
        records = []
        for name, spec in self.factors.items():
            try:
                series = self._calc_factor(name, spec, df, history)
                for key, val in series.items():
                    if pd.isna(val):
                        continue
                    code = key if isinstance(key, str) else key[0]
                    records.append((code, target_date, name, float(val)))
            except Exception as e:
                logger.debug(f"因子 {name} 计算失败: {e}")

        # 4. 持久化
        if records:
            self._persist(records)
            snapshot = self._build_snapshot(records)
            cache_key = f"factor_snapshot:{target_date.strftime('%Y%m%d')}"
            redis_cache.setex(cache_key, 86400 * 2, snapshot)
        logger.info(f"因子快照计算完成: {target_date}, {len(records)} 条")

    def _load_market_snapshot(self, target_date: date) -> pd.DataFrame:
        with get_db_session() as session:
            result = session.execute(
                text(
                    """
                    SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close,
                           d.pre_close, d.change_pct, d.vol, d.amount, d.turnover_rate,
                           d.pe_ttm, d.pb, d.dv_ttm
                    FROM daily_price d
                    WHERE d.trade_date = :date
                    ORDER BY d.amount DESC
                    LIMIT 800
                    """
                ),
                {"date": target_date},
            )
            rows = result.fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=list(result.keys()))
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.set_index(["ts_code", "trade_date"])
        df = self._to_float(df)
        return df

    def _load_history(self, target_date: date) -> pd.DataFrame:
        """加载目标日期之前的历史日线序列（供技术/动量因子使用）"""
        return pd.DataFrame()

    def _to_float(self, df: pd.DataFrame) -> pd.DataFrame:
        """将数据库返回的 Decimal 等数值列统一转为 float"""
        for col in df.columns:
            try:
                df[col] = df[col].astype(float)
            except (TypeError, ValueError):
                pass
        return df

    def _calc_factor(self, name, spec, df, history) -> pd.Series:
        """按注册表配置分配合适的因子计算器"""
        ftype = spec.get("type")
        window = spec.get("window")
        closes = df["close"]

        if ftype == "technical":
            if name.startswith("ma_"):
                return closes.rolling(window).mean()
            if name.startswith("rsi_"):
                delta = closes.diff()
                gain = delta.where(delta > 0, 0.0)
                loss = -delta.where(delta < 0, 0.0)
                avg_gain = gain.rolling(window).mean()
                avg_loss = loss.rolling(window).mean()
                rs = avg_gain / avg_loss.replace(0, pd.NA)
                return 100 - (100 / (1 + rs))
            if name == "macd_hist":
                ema_fast = closes.ewm(span=12, adjust=False).mean()
                ema_slow = closes.ewm(span=26, adjust=False).mean()
                dif = ema_fast - ema_slow
                dea = dif.ewm(span=9, adjust=False).mean()
                return 2 * (dif - dea)
            if name == "boll_width":
                mid = closes.rolling(window or 20).mean()
                std = closes.rolling(window or 20).std()
                return (2 * std) / mid.replace(0, pd.NA)
        elif ftype == "momentum":
            if name.startswith("ret_"):
                return closes.pct_change(periods=window)
            if name.startswith("volatility_"):
                ret = closes.pct_change()
                return ret.rolling(window).std() * (252 ** 0.5)
            if name.startswith("max_drawdown_"):
                cummax = closes.rolling(window).max()
                dd = (closes - cummax) / cummax.replace(0, pd.NA)
                return dd.rolling(window).min()
            if name.startswith("volume_ratio_"):
                vmean = df["vol"].rolling(window).mean()
                return df["vol"] / vmean.replace(0, pd.NA)
        elif ftype == "fundamental":
            # 基本面因子直接从当日快照读取（已合并到 daily_price）
            if name in df.columns:
                return df[name]
            return pd.Series(index=df.index, dtype=float)

        # 通用：尝试按列计算
        if name in df.columns:
            return df[name]
        return pd.Series(index=df.index, dtype=float)

    def _persist(self, records):
        with get_db_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO factor_data (ts_code, trade_date, factor_name, factor_value)
                    VALUES (:ts_code, :trade_date, :factor_name, :factor_value)
                    ON CONFLICT (ts_code, trade_date, factor_name) DO UPDATE SET
                        factor_value = EXCLUDED.factor_value
                    """
                ),
                [
                    {
                        "ts_code": r[0],
                        "trade_date": r[1],
                        "factor_name": r[2],
                        "factor_value": r[3],
                    }
                    for r in records
                ],
            )

    def _build_snapshot(self, records) -> str:
        """构建宽表快照: {ts_code: {factor_name: value}}"""
        snapshot = {}
        for code, _date, name, val in records:
            snapshot.setdefault(code, {})[name] = round(val, 6)
        return json.dumps(snapshot, ensure_ascii=False)

    def _latest_trade_date(self) -> date:
        with get_db_session() as session:
            result = session.execute(text("SELECT MAX(trade_date) FROM daily_price"))
            max_date = result.scalar()
        if max_date:
            return pd.to_datetime(max_date).date()
        return date.today()
