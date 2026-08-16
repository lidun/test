"""Tushare 数据源实现"""
from __future__ import annotations

import time

import pandas as pd
from loguru import logger

from src.core.config import config
from src.data.base_provider import DataProvider


class TushareDataProvider(DataProvider):
    name = "tushare"

    def __init__(self):
        self._pro = None
        self._available = False
        if config.data.tushare_token:
            try:
                import tushare as ts

                ts.set_token(config.data.tushare_token)
                self._pro = ts.pro_api()
                self._available = True
            except Exception as e:
                logger.error(f"Tushare 初始化失败: {e}")
        else:
            logger.warning("未配置 TUSHARE_TOKEN，Tushare 数据源不可用")

    @property
    def available(self) -> bool:
        return self._available

    def _safe_call(self, func, max_retries: int = 3, **kwargs):
        for attempt in range(max_retries):
            try:
                result = func(**kwargs)
                if result is not None and not result.empty:
                    return result
                logger.warning(f"API返回空数据 (attempt {attempt+1})")
            except Exception as e:
                logger.warning(f"API调用失败 (attempt {attempt+1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
        return None

    def fetch_stock_basic(self) -> pd.DataFrame:
        if not self._available:
            return pd.DataFrame()
        data = self._safe_call(
            self._pro.stock_basic,
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,area,industry,market,list_date,is_hs",
        )
        if data is not None:
            data = data[
                data["ts_code"].str.endswith(".SH") | data["ts_code"].str.endswith(".SZ")
            ]
            data = data[data["ts_code"].str.startswith(("60", "00", "30", "68"))]
        return data if data is not None else pd.DataFrame()

    def fetch_daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        if not self._available:
            return pd.DataFrame()
        return self._safe_call(
            self._pro.daily, ts_code=ts_code, start_date=start_date, end_date=end_date
        )

    def fetch_daily_batch(self, trade_date: str) -> pd.DataFrame:
        if not self._available:
            return pd.DataFrame()
        return self._safe_call(self._pro.daily, trade_date=trade_date)

    def fetch_daily_basic(self, trade_date: str) -> pd.DataFrame:
        if not self._available:
            return pd.DataFrame()
        return self._safe_call(
            self._pro.daily_basic,
            trade_date=trade_date,
            fields="ts_code,trade_date,turnover_rate,pe_ttm,pb,ps_ttm,dv_ttm,total_mv",
        )

    def fetch_income(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        if not self._available:
            return pd.DataFrame()
        return self._safe_call(
            self._pro.income, ts_code=ts_code, start_date=start_date, end_date=end_date
        )

    def fetch_balance_sheet(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        if not self._available:
            return pd.DataFrame()
        return self._safe_call(
            self._pro.balancesheet,
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )

    def fetch_moneyflow(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        if not self._available:
            return pd.DataFrame()
        return self._safe_call(
            self._pro.moneyflow, ts_code=ts_code, start_date=start_date, end_date=end_date
        )
