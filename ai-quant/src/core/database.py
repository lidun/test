"""数据库管理模块：SQLAlchemy 引擎与会话管理"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from loguru import logger
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.core.config import config

engine = create_engine(
    config.db.url,
    # 2G 内存服务器适配：小连接池，避免每个 Postgres 后端进程占用大量内存
    pool_size=3,
    max_overflow=2,
    pool_pre_ping=True,
    pool_recycle=1800,
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@contextmanager
def get_db_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_database() -> None:
    """初始化数据库：建库并执行 schema.sql"""
    from sqlalchemy.engine import make_url

    url = make_url(config.db.url)
    admin_url = url.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :db"),
                {"db": url.database},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{url.database}"'))
                logger.info(f"数据库 {url.database} 创建成功")
    finally:
        admin_engine.dispose()

    schema_path = config.PROJECT_ROOT / "src" / "data" / "schema.sql"
    if not schema_path.exists():
        logger.warning(f"schema 文件不存在: {schema_path}")
        return
    with engine.begin() as conn:
        conn.execute(text(schema_path.read_text(encoding="utf-8")))
    # 迁移：清空旧体系（进化/策略自动生成/报告）遗留的表
    with engine.begin() as conn:
        conn.execute(
            text(
                "DROP TABLE IF EXISTS evolution_log, strategy_performance, strategies, "
                "factor_data, report_history CASCADE"
            )
        )
    # 兼容升级：为已存在的 daily_price 表补充估值列
    with engine.begin() as conn:
        for col, ctype in [
            ("pe_ttm", "DECIMAL(12,3)"),
            ("pb", "DECIMAL(12,3)"),
            ("dv_ttm", "DECIMAL(12,3)"),
        ]:
            conn.execute(
                text(
                    f"ALTER TABLE daily_price ADD COLUMN IF NOT EXISTS {col} {ctype}"
                )
            )
    logger.info("数据库表结构初始化完成")


def check_connection() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"数据库连接失败: {e}")
        return False
