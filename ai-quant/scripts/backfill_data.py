"""回填历史数据脚本: python scripts/backfill_data.py --years 3 [--codes 000001.SZ,600000.SH]"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.config import setup_logging  # noqa: E402

setup_logging()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--codes", type=str, default="", help="逗号分隔的股票代码")
    args = parser.parse_args()

    from src.web.context import AppContext

    ctx = AppContext.get()
    ctx.init(with_knowledge=False)
    codes = [c.strip() for c in args.codes.split(",") if c.strip()] or None
    await ctx.data_pipeline.backfill_history(years=args.years, codes=codes)


if __name__ == "__main__":
    asyncio.run(main())
