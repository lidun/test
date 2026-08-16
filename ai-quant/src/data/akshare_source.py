"""Akshare 免费数据源实现（无 Token 即可使用）

提供：
- 股票基础信息
- 全市场实时/当日行情快照
- 单股历史日线（前复权）
- 每日基本面指标（市盈率、市净率、换手率等）
"""
from __future__ import annotations

import time
from datetime import date

import pandas as pd
from loguru import logger

from src.core.config import config
from src.data.base_provider import DataProvider

try:
    import akshare as ak
except ImportError:
    ak = None


class AkshareDataProvider(DataProvider):
    name = "akshare"

    def __init__(self):
        self._available = ak is not None

    @property
    def available(self) -> bool:
        return self._available

    def _safe_call(self, func, max_retries: int = 3, **kwargs):
        for attempt in range(max_retries):
            try:
                result = func(**kwargs)
                if result is not None and not result.empty:
                    return result
                logger.warning(f"Akshare API返回空数据 (attempt {attempt+1})")
            except Exception as e:
                logger.warning(f"Akshare API调用失败 (attempt {attempt+1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
        return None

    def fetch_stock_basic(self) -> pd.DataFrame:
        """获取 A 股股票列表。akshare 返回 columns: code, name"""
        if not self._available:
            return pd.DataFrame()
        data = self._safe_call(ak.stock_info_a_code_name)
        if data is None or data.empty:
            return pd.DataFrame()
        data = data.rename(columns={"code": "symbol", "name": "name"})
        data["ts_code"] = data["symbol"].apply(self._normalize_ts_code)
        data = data[["ts_code", "symbol", "name"]]
        return data

    def fetch_daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """获取单只股票日线。start_date/end_date 格式: 20230101"""
        if not self._available:
            return pd.DataFrame()
        symbol = ts_code.split(".")[0]
        data = self._safe_call(
            ak.stock_zh_a_hist,
            symbol=symbol,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
        if data is None or data.empty:
            return pd.DataFrame()
        rename_map = {
            "日期": "trade_date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "vol",
            "成交额": "amount",
            "振幅": "amplitude",
            "涨跌幅": "pct_chg",
            "涨跌额": "change",
            "换手率": "turnover_rate",
        }
        data = data.rename(columns=rename_map)
        if "trade_date" not in data.columns:
            return pd.DataFrame()
        data["trade_date"] = pd.to_datetime(data["trade_date"])
        data["ts_code"] = ts_code
        return data

    def fetch_daily_batch(self, trade_date: str) -> pd.DataFrame:
        """获取全市场行情。akshare 提供当日实时快照。
        仅当 trade_date 为今天时返回实时快照，否则返回空表（历史需逐股回填）。
        """
        if not self._available:
            return pd.DataFrame()
        today = date.today().strftime("%Y%m%d")
        if trade_date != today:
            logger.warning("Akshare 无法按历史日期批量拉取，返回空表")
            return pd.DataFrame()
        data = self._safe_call(ak.stock_zh_a_spot_em)
        if data is None or data.empty:
            return pd.DataFrame()
        rename_map = {
            "代码": "symbol",
            "名称": "name",
            "最新价": "close",
            "涨跌幅": "pct_chg",
            "涨跌额": "change",
            "成交量": "vol",
            "成交额": "amount",
            "今开": "open",
            "最高": "high",
            "最低": "low",
            "昨收": "pre_close",
            "换手率": "turnover_rate",
            "市盈率-动态": "pe_ttm",
            "市净率": "pb",
        }
        data = data.rename(columns=rename_map)
        data["ts_code"] = data["symbol"].apply(self._normalize_ts_code)
        data["trade_date"] = pd.to_datetime(trade_date, format="%Y%m%d")
        keep = [
            "ts_code",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "pct_chg",
            "vol",
            "amount",
            "turnover_rate",
        ]
        keep = [c for c in keep if c in data.columns]
        return data[keep]

    def fetch_daily_basic(self, trade_date: str) -> pd.DataFrame:
        """获取全市场每日基本面指标（当前仅实时快照可用）"""
        return self.fetch_daily_batch(trade_date)

    def fetch_income(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        logger.info(f"Akshare 暂不提供利润表按日批量接口 (ts_code={ts_code})")
        return pd.DataFrame()

    def fetch_balance_sheet(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_moneyflow(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        if not self._available:
            return pd.DataFrame()
        symbol = ts_code.split(".")[0]
        data = self._safe_call(ak.stock_individual_fund_flow, stock=symbol, market="sh" if ts_code.endswith("SH") else "sz")
        if data is None or data.empty:
            return pd.DataFrame()
        return data.tail(120)
