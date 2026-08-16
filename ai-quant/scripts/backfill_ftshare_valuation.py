"""FTShare 真实估值回填：覆盖 TOP60 只股票近 1 年合成估值为真实 pe/pb"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date, timedelta

from loguru import logger
from sqlalchemy import text

from src.core.database import get_db_session
from src.data.ftshare_source import FTShareDataProvider
from src.data.data_pipeline import _num


def main():
    provider = FTShareDataProvider()
    if not provider.available:
        logger.error("FTShare 不可用")
        return
    end = date.today()
    start = end - timedelta(days=365)
    with get_db_session() as session:
        codes = [
            r[0]
            for r in session.execute(
                text("SELECT ts_code FROM stock_basic LIMIT 60")
            ).fetchall()
        ]
    total_updated = 0
    for i, code in enumerate(codes):
        df = provider.fetch_valuation_history(code, max_pages=4)
        if df is None or df.empty:
            logger.warning(f"{code}: 无估值数据")
            continue
        df = df[
            (df["trade_date"].dt.date >= start)
            & (df["trade_date"].dt.date <= end)
        ]
        if df.empty:
            continue
        updated = 0
        with get_db_session() as session:
            for _, row in df.iterrows():
                r = session.execute(
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
                updated += r.rowcount
        total_updated += updated
        logger.info(f"[{i+1}/{len(codes)}] {code}: 更新 {updated} 条")
    logger.info(f"FTShare 估值回填完成: 共更新 {total_updated} 条")


if __name__ == "__main__":
    main()
