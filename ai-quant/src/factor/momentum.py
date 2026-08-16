"""动量与风险因子"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.factor.base_factor import BaseFactor


class MomentumFactor(BaseFactor):
    name = "momentum"
    description = "动量因子：N日收益率"

    def __init__(self, window: int = 20):
        self.window = window
        self.name = f"ret_{window}d"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        return data["close"].pct_change(periods=self.window)


class VolatilityFactor(BaseFactor):
    name = "volatility"
    description = "波动率因子（年化）"

    def __init__(self, window: int = 20):
        self.window = window
        self.name = f"volatility_{window}d"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        returns = data["close"].pct_change()
        return returns.rolling(window=self.window).std() * np.sqrt(252)


class MaxDrawdownFactor(BaseFactor):
    name = "max_drawdown"
    description = "最大回撤因子"

    def __init__(self, window: int = 60):
        self.window = window
        self.name = f"max_drawdown_{window}d"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        cummax = data["close"].rolling(self.window).max()
        drawdown = (data["close"] - cummax) / cummax.replace(0, np.nan)
        return drawdown.rolling(self.window).min()


class VolumeRatioFactor(BaseFactor):
    name = "volume_ratio"
    description = "量比因子：当前成交量相对 N 日均值"

    def __init__(self, window: int = 5):
        self.window = window
        self.name = f"volume_ratio_{window}d"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        vol_mean = data["vol"].rolling(self.window).mean()
        return data["vol"] / vol_mean.replace(0, np.nan)
