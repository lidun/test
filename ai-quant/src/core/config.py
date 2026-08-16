"""全局配置加载模块

从 .env 文件读取配置，支持环境变量覆盖。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _get_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _get_float(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _get_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    try:
        return int(val)
    except ValueError:
        return default


class ProviderConfig:
    """单个 LLM 服务商配置（OpenAI 兼容协议）"""

    def __init__(self, prefix: str):
        self.api_key = os.getenv(f"{prefix}_API_KEY", "")
        self.base_url = os.getenv(f"{prefix}_BASE_URL", "")
        self.model = os.getenv(f"{prefix}_MODEL", "")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def to_dict(self) -> dict:
        return {
            "api_key": self.api_key,
            "base_url": self.base_url,
            "model": self.model,
            "available": self.available,
        }


class LLMConfig:
    def __init__(self):
        self.default_provider = os.getenv("DEFAULT_LLM_PROVIDER", "deepseek")
        self.providers = {
            "deepseek": ProviderConfig("DEEPSEEK"),
            "qwen": ProviderConfig("QWEN"),
            "moonshot": ProviderConfig("MOONSHOT"),
            "glm": ProviderConfig("GLM"),
            "baidu": ProviderConfig("BAIDU"),
            "ollama": ProviderConfig("OLLAMA"),
            "openai": ProviderConfig("OPENAI"),
        }
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.anthropic_model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
        self.max_tokens = _get_int("LLM_MAX_TOKENS", 4096)
        self.temperature_generate = _get_float("LLM_TEMPERATURE_GENERATE", 0.8)
        self.temperature_mutate = _get_float("LLM_TEMPERATURE_MUTATE", 0.6)
        self.temperature_analyze = _get_float("LLM_TEMPERATURE_ANALYZE", 0.3)
        self.request_timeout = _get_int("LLM_REQUEST_TIMEOUT", 120)

    @property
    def default(self) -> ProviderConfig:
        return self.providers.get(self.default_provider) or ProviderConfig("OPENAI")

    def available_providers(self) -> list[str]:
        return [name for name, cfg in self.providers.items() if cfg.available]


class DataConfig:
    def __init__(self):
        self.tushare_token = os.getenv("TUSHARE_TOKEN", "")
        self.primary_provider = os.getenv("PRIMARY_DATA_PROVIDER", "tushare")


class DBConfig:
    def __init__(self):
        self.host = os.getenv("POSTGRES_HOST", "localhost")
        self.port = _get_int("POSTGRES_PORT", 5432)
        self.database = os.getenv("POSTGRES_DB", "ai_quant")
        self.user = os.getenv("POSTGRES_USER", "ai_quant_user")
        self.password = os.getenv("POSTGRES_PASSWORD", "AiQuant2024!")

    @property
    def url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )


class RedisConfig:
    def __init__(self):
        self.host = os.getenv("REDIS_HOST", "localhost")
        self.port = _get_int("REDIS_PORT", 6379)
        self.db = _get_int("REDIS_DB", 0)

    @property
    def url(self) -> str:
        return f"redis://{self.host}:{self.port}/{self.db}"


class WebConfig:
    def __init__(self):
        self.host = os.getenv("WEB_HOST", "0.0.0.0")
        self.port = _get_int("WEB_PORT", 8000)
        self.username = os.getenv("WEB_USERNAME", "admin")
        self.password = os.getenv("WEB_PASSWORD", "admin123")


class StrategyConfig:
    def __init__(self):
        self.max_active_strategies = _get_int("MAX_STRATEGIES", 20)
        self.initial_capital = _get_float("INITIAL_CAPITAL", 100000.0)
        self.commission_rate = _get_float("COMMISSION_RATE", 0.0003)
        self.slippage = _get_float("SLIPPAGE", 0.001)
        self.min_sharpe_for_pass = _get_float("MIN_SHARPE_FOR_PASS", 0.3)
        self.min_win_rate = _get_float("MIN_WIN_RATE", 0.45)
        self.history_validation_years = _get_int("HISTORY_VALIDATION_YEARS", 3)
        self.max_conditions = _get_int("MAX_CONDITIONS", 8)


class EvolutionConfig:
    def __init__(self):
        self.cycle_days = _get_int("EVOLUTION_CYCLE_DAYS", 7)
        self.elimination_rate = _get_float("EVOLUTION_ELIMINATION_RATE", 0.20)
        self.elite_count = _get_int("EVOLUTION_ELITE_COUNT", 3)
        self.mutants_per_elite = _get_int("EVOLUTION_MUTANTS_PER_ELITE", 2)
        self.new_strategies_per_cycle = _get_int("EVOLUTION_NEW_STRATEGIES_PER_CYCLE", 3)
        self.enable_crossover = _get_bool("EVOLUTION_ENABLE_CROSSOVER", True)


class AlertConfig:
    def __init__(self):
        self.dingtalk_webhook = os.getenv("DINGTALK_WEBHOOK", "")
        self.email_alert = os.getenv("EMAIL_ALERT", "")
        self.max_drawdown_alert = _get_float("ALERT_MAX_DRAWDOWN", -0.20)
        self.daily_loss_alert = _get_float("ALERT_DAILY_LOSS", -0.05)
        self.consecutive_loss_days = _get_int("ALERT_CONSECUTIVE_LOSS_DAYS", 5)


class KnowledgeConfig:
    def __init__(self):
        self.chroma_persist_path = os.getenv("CHROMA_PERSIST_PATH", "./data/chroma")
        self.embedding_mode = os.getenv("EMBEDDING_MODE", "builtin")
        self.embedding_model = os.getenv("EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5")


class AppConfig:
    """聚合所有子配置"""

    def __init__(self):
        self.PROJECT_ROOT = PROJECT_ROOT
        self.DATA_DIR = PROJECT_ROOT / "data"
        self.LOG_DIR = PROJECT_ROOT / "logs"
        self.CONFIG_DIR = PROJECT_ROOT / "config"
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        self.llm = LLMConfig()
        self.data = DataConfig()
        self.db = DBConfig()
        self.redis = RedisConfig()
        self.web = WebConfig()
        self.strategy = StrategyConfig()
        self.evolution = EvolutionConfig()
        self.alert = AlertConfig()
        self.knowledge = KnowledgeConfig()

    def ensure_dirs(self):
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.LOG_DIR.mkdir(parents=True, exist_ok=True)
        Path(self.knowledge.chroma_persist_path).expanduser().parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    cfg = AppConfig()
    cfg.ensure_dirs()
    return cfg


config = get_config()


def setup_logging():
    from loguru import logger as _logger

    _logger.remove()
    log_file = config.LOG_DIR / "ai_quant.log"
    _logger.add(
        log_file,
        rotation="10 MB",
        retention="30 days",
        level=config.log_level,
        encoding="utf-8",
    )
    _logger.add(
        lambda msg: print(msg, end=""),
        level=config.log_level,
    )
    return _logger
