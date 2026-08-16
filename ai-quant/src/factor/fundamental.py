"""基本面因子：PE/PB/ROE/营收增速等

基本面数据来自 daily_basic 表（pe_ttm/pb/turnover_rate）与
可选财务数据（roe、增速等，来源未填充时为 NaN）。
"""
from __future__ import annotations

import pandas as pd

from src.factor.base_factor import BaseFactor


class PEFactor(BaseFactor):
    name = "pe_ttm"
    description = "市盈率（TTM）"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if "pe_ttm" not in data.columns:
            return pd.Series(index=data.index, dtype=float)
        return data["pe_ttm"].replace(0, pd.NA)


class PBFactor(BaseFactor):
    name = "pb"
    description = "市净率"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if "pb" not in data.columns:
            return pd.Series(index=data.index, dtype=float)
        return data["pb"].replace(0, pd.NA)


class DividendYieldFactor(BaseFactor):
    name = "dividend_yield"
    description = "股息率"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if "dv_ttm" not in data.columns:
            return pd.Series(index=data.index, dtype=float)
        return data["dv_ttm"]


class ROEFactor(BaseFactor):
    name = "roe"
    description = "净资产收益率"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if "roe" not in data.columns:
            return pd.Series(index=data.index, dtype=float)
        return data["roe"]
