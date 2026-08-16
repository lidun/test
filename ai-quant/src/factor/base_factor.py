"""因子基类"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

import pandas as pd


class BaseFactor(ABC):
    name: str = ""
    category: str = ""
    description: str = ""
    dependencies: List[str] = []
    lookback_days: int = 252

    @abstractmethod
    def calculate(self, data: pd.DataFrame) -> pd.Series | pd.DataFrame:
        pass

    def validate(self, result) -> bool:
        if result is None:
            return False
        if hasattr(result, "isna") and result.isna().all():
            return False
        if hasattr(result, "__iter__"):
            try:
                if (result == 0).all():
                    return False
            except (TypeError, ValueError):
                pass
        return True

    def winsorize(self, series: pd.Series, limits: tuple = (0.01, 0.99)) -> pd.Series:
        lower = series.quantile(limits[0])
        upper = series.quantile(limits[1])
        return series.clip(lower, upper)

    def standardize(self, series: pd.Series) -> pd.Series:
        mean = series.mean()
        std = series.std()
        if std is None or std == 0 or pd.isna(std):
            return pd.Series(0, index=series.index)
        return (series - mean) / std
