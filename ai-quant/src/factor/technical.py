"""技术因子：均线、RSI、MACD、布林带等"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.factor.base_factor import BaseFactor


class MAFactor(BaseFactor):
    name = "ma"
    description = "均线因子"

    def __init__(self, window: int = 20):
        self.window = window
        self.name = f"ma_{window}"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        return data["close"].rolling(window=self.window).mean()


class EMAFactor(BaseFactor):
    name = "ema"
    description = "指数移动平均因子"

    def __init__(self, window: int = 20):
        self.window = window
        self.name = f"ema_{window}"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        return data["close"].ewm(span=self.window, adjust=False).mean()


class RSIFactor(BaseFactor):
    name = "rsi"
    description = "RSI相对强弱指标"

    def __init__(self, window: int = 14):
        self.window = window
        self.name = f"rsi_{window}"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        delta = data["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(self.window).mean()
        avg_loss = loss.rolling(self.window).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi


class MACDFactor(BaseFactor):
    name = "macd"
    description = "MACD指标"

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def calculate(self, data: pd.DataFrame) -> pd.DataFrame:
        ema_fast = data["close"].ewm(span=self.fast, adjust=False).mean()
        ema_slow = data["close"].ewm(span=self.slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=self.signal, adjust=False).mean()
        hist = 2 * (dif - dea)
        return pd.DataFrame({"dif": dif, "dea": dea, "hist": hist})


class BOLLFactor(BaseFactor):
    name = "boll"
    description = "布林带因子"

    def __init__(self, window: int = 20, num_std: float = 2.0):
        self.window = window
        self.num_std = num_std
        self.name = f"boll_{window}"

    def calculate(self, data: pd.DataFrame) -> pd.DataFrame:
        mid = data["close"].rolling(self.window).mean()
        std = data["close"].rolling(self.window).std()
        upper = mid + self.num_std * std
        lower = mid - self.num_std * std
        width = (upper - lower) / mid.replace(0, np.nan)
        return pd.DataFrame({"upper": upper, "mid": mid, "lower": lower, "boll_width": width})


class CloseMaRatioFactor(BaseFactor):
    name = "close_ma_ratio"
    description = "价格相对均线位置因子"

    def __init__(self, window: int = 20):
        self.window = window
        self.name = f"close_ma_{window}"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        ma = data["close"].rolling(self.window).mean()
        return data["close"] / ma.replace(0, np.nan) - 1
