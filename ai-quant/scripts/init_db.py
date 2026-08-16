"""初始化数据库脚本: python scripts/init_db.py"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.database import init_database  # noqa: E402
from src.core.config import setup_logging  # noqa: E402

setup_logging()


def main():
    init_database()
    print("数据库初始化完成")


if __name__ == "__main__":
    main()
