"""数据提供方抽象基类"""
from __future__ import annotations

import pandas as pd
from loguru import logger


class DataProvider:
    """统一数据源接口。所有具体数据源（Tushare、Akshare）必须实现这些方法。"""

    name = "base"

    def fetch_stock_basic(self) -> pd.DataFrame:
        """获取股票基础信息（代码、名称、行业等）"""
        raise NotImplementedError

    def fetch_daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """获取单只股票日线行情"""
        raise NotImplementedError

    def fetch_daily_batch(self, trade_date: str) -> pd.DataFrame:
        """获取某交易日全市场行情"""
        raise NotImplementedError

    def fetch_income(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """获取利润表"""
        return pd.DataFrame()

    def fetch_balance_sheet(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """获取资产负债表"""
        return pd.DataFrame()

    def fetch_moneyflow(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """获取资金流向"""
        return pd.DataFrame()

    def fetch_daily_basic(self, trade_date: str) -> pd.DataFrame:
        """获取每日指标（换手率、市盈率等）"""
        return pd.DataFrame()

    def _normalize_ts_code(self, code: str) -> str:
        """将 600000 等纯数字代码规范为 600000.SH / 000001.SZ 格式"""
        code = code.strip().upper()
        if "." in code:
            return code
        if len(code) != 6:
            return code
        if code.startswith(("6", "9")):
            return f"{code}.SH"
        return f"{code}.SZ"
