"""运行时配置存储：system_config 表（KV）读写 + .env 持久化

优先级：数据库配置 > .env 环境变量。配置保存时同步写入 .env，
保证重启后仍生效；LLM 相关配置通过 factory 每次读取 DB，保存即生效。
"""
from __future__ import annotations

import os as _os
from pathlib import Path

import requests
from loguru import logger
from sqlalchemy import text

from src.core.config import PROJECT_ROOT, config
from src.core.database import get_db_session

# 配置项元数据：(key, category, description, default)
CONFIG_SCHEMA: list[tuple[str, str, str, str]] = [
    # ---- LLM 配置 ----
    ("llm.default_provider", "llm", "默认大模型服务商 (deepseek/qwen/moonshot/glm/baidu/ollama/openai)", "deepseek"),
    ("llm.deepseek_api_key", "llm", "DeepSeek API Key", ""),
    ("llm.deepseek_base_url", "llm", "DeepSeek Base URL", "https://api.deepseek.com/v1"),
    ("llm.deepseek_model", "llm", "DeepSeek 模型", "deepseek-chat"),
    ("llm.qwen_api_key", "llm", "通义千问 API Key", ""),
    ("llm.qwen_base_url", "llm", "通义千问 Base URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ("llm.qwen_model", "llm", "通义千问模型", "qwen-plus"),
    ("llm.moonshot_api_key", "llm", "Moonshot API Key", ""),
    ("llm.moonshot_base_url", "llm", "Moonshot Base URL", "https://api.moonshot.cn/v1"),
    ("llm.moonshot_model", "llm", "Moonshot 模型", "moonshot-v1-8k"),
    ("llm.glm_api_key", "llm", "智谱 GLM API Key", ""),
    ("llm.glm_base_url", "llm", "智谱 GLM Base URL", "https://open.bigmodel.cn/api/paas/v4"),
    ("llm.glm_model", "llm", "智谱 GLM 模型", "glm-4-flash"),
    ("llm.ollama_base_url", "llm", "本地 Ollama Base URL", "http://localhost:11434/v1"),
    ("llm.ollama_model", "llm", "本地 Ollama 模型", "qwen2.5:7b"),
    ("llm.openai_api_key", "llm", "OpenAI API Key", ""),
    ("llm.openai_base_url", "llm", "OpenAI Base URL", "https://api.openai.com/v1"),
    ("llm.openai_model", "llm", "OpenAI 模型", "gpt-4o-mini"),
    ("llm.max_tokens", "llm", "LLM 最大生成 Token 数", "4096"),
    ("llm.temperature_generate", "llm", "生成新策略温度 (0-2)", "0.8"),
    ("llm.request_timeout", "llm", "LLM 请求超时(秒)", "120"),
    # ---- 系统运行时间 ----
    ("system.daily_data_time", "system", "每日行情更新时间 (HH:MM)", "15:30"),
    ("system.daily_sim_time", "system", "每日模拟交易时间 (HH:MM)", "16:00"),
    ("system.daily_alert_time", "system", "每日告警检查时间 (HH:MM)", "16:30"),
    ("system.daily_report_time", "system", "每日报告生成时间 (HH:MM)", "17:00"),
    ("system.weekly_evolve_time", "system", "每周进化时间 (HH:MM)", "10:00"),
    ("system.weekly_report_time", "system", "每周报告时间 (HH:MM)", "18:00"),
    ("system.weekly_evolve_day", "system", "每周进化日 (0-6, 0=周一)", "5"),
    ("system.health_check_interval", "system", "健康检查间隔(小时)", "1"),
    # ---- 数据源 ----
    ("data.primary_provider", "data", "主数据源 (akshare/tushare/ftshare)", "akshare"),
    ("data.tushare_token", "data", "Tushare Token", ""),
    # ---- 进化与策略 ----
    ("evolution.cycle_days", "evolution", "进化周期(天)", "7"),
    ("evolution.elimination_rate", "evolution", "每轮淘汰比例 (0-1)", "0.20"),
    ("evolution.elite_count", "evolution", "精英保留数量", "3"),
    ("evolution.mutants_per_elite", "evolution", "每精英变异数", "2"),
    ("evolution.new_strategies_per_cycle", "evolution", "每轮新生策略数", "3"),
    ("evolution.enable_crossover", "evolution", "启用杂交", "true"),
    ("strategy.max_active_strategies", "strategy", "竞技场最大策略数", "20"),
    ("strategy.initial_capital", "strategy", "初始资金", "100000"),
    ("strategy.commission_rate", "strategy", "手续费率", "0.0003"),
    ("strategy.slippage", "strategy", "滑点", "0.001"),
    # ---- 告警 ----
    ("alert.max_drawdown", "alert", "最大回撤告警阈值 (负数)", "-0.20"),
    ("alert.daily_loss", "alert", "单日亏损告警阈值 (负数)", "-0.05"),
    ("alert.consecutive_loss_days", "alert", "连续亏损天数告警", "5"),
]

_ENV_MAP: dict[str, str] = {
    "llm.deepseek_api_key": "DEEPSEEK_API_KEY",
    "llm.deepseek_base_url": "DEEPSEEK_BASE_URL",
    "llm.deepseek_model": "DEEPSEEK_MODEL",
    "llm.qwen_api_key": "QWEN_API_KEY",
    "llm.qwen_base_url": "QWEN_BASE_URL",
    "llm.qwen_model": "QWEN_MODEL",
    "llm.moonshot_api_key": "MOONSHOT_API_KEY",
    "llm.moonshot_base_url": "MOONSHOT_BASE_URL",
    "llm.moonshot_model": "MOONSHOT_MODEL",
    "llm.glm_api_key": "GLM_API_KEY",
    "llm.glm_base_url": "GLM_BASE_URL",
    "llm.glm_model": "GLM_MODEL",
    "llm.ollama_base_url": "OLLAMA_BASE_URL",
    "llm.ollama_model": "OLLAMA_MODEL",
    "llm.openai_api_key": "OPENAI_API_KEY",
    "llm.openai_base_url": "OPENAI_BASE_URL",
    "llm.openai_model": "OPENAI_MODEL",
    "llm.max_tokens": "LLM_MAX_TOKENS",
    "llm.temperature_generate": "LLM_TEMPERATURE_GENERATE",
    "llm.request_timeout": "LLM_REQUEST_TIMEOUT",
    "data.primary_provider": "PRIMARY_DATA_PROVIDER",
    "data.tushare_token": "TUSHARE_TOKEN",
    "evolution.cycle_days": "EVOLUTION_CYCLE_DAYS",
    "evolution.elimination_rate": "EVOLUTION_ELIMINATION_RATE",
    "evolution.elite_count": "EVOLUTION_ELITE_COUNT",
    "evolution.mutants_per_elite": "EVOLUTION_MUTANTS_PER_ELITE",
    "evolution.new_strategies_per_cycle": "EVOLUTION_NEW_STRATEGIES_PER_CYCLE",
    "evolution.enable_crossover": "EVOLUTION_ENABLE_CROSSOVER",
    "strategy.max_active_strategies": "MAX_STRATEGIES",
    "strategy.initial_capital": "INITIAL_CAPITAL",
    "strategy.commission_rate": "COMMISSION_RATE",
    "strategy.slippage": "SLIPPAGE",
    "alert.max_drawdown": "ALERT_MAX_DRAWDOWN",
    "alert.daily_loss": "ALERT_DAILY_LOSS",
    "alert.consecutive_loss_days": "ALERT_CONSECUTIVE_LOSS_DAYS",
}

# 各服务商可选模型版本（用于前端下拉选择）
MODEL_OPTIONS: dict[str, list[str]] = {
    "deepseek": [
        "deepseek-chat",
        "deepseek-reasoner",
    ],
    "qwen": [
        "qwen-plus",
        "qwen-turbo",
        "qwen-max",
        "qwen-long",
        "qwen2.5-72b-instruct",
        "qwen3-32b",
        "qwen2.5-vl-72b-instruct",
    ],
    "moonshot": [
        "moonshot-v1-8k",
        "moonshot-v1-32k",
        "moonshot-v1-128k",
        "kimi-latest",
        "kimi-k2-0711-preview",
    ],
    "glm": [
        "glm-4-plus",
        "glm-4-air",
        "glm-4-flash",
        "glm-4-long",
        "glm-4v-plus",
        "glm-4.5",
        "glm-4.5-air",
    ],
    "ollama": [
        "qwen2.5:7b",
        "qwen3:8b",
        "qwen2.5:14b",
        "llama3.1:8b",
        "deepseek-r1:7b",
        "glm4:9b",
        "yi:6b",
    ],
    "openai": [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "gpt-3.5-turbo",
        "o3-mini",
        "o1",
    ],
}

# 需要打码展示的敏感 key
_SECRET_KEYS = {"llm.default_provider", "llm.max_tokens"}


def _is_secret(key: str) -> bool:
    return "api_key" in key or "token" in key


class ConfigStore:
    def __init__(self):
        self.env_path = PROJECT_ROOT / ".env"

    def get_all(self) -> dict[str, str]:
        """返回所有配置项的 DB 值（缺省回退 .env/默认值）"""
        env_vals = {k: os_getenv(v) for k, v in _ENV_MAP.items() if os_getenv(v)}
        try:
            with get_db_session() as session:
                rows = session.execute(
                    text("SELECT key, value FROM system_config")
                ).fetchall()
            db_vals = {r[0]: r[1] for r in rows}
        except Exception as e:
            logger.warning(f"读取 system_config 失败: {e}")
            db_vals = {}
        result = {}
        for key, _cat, _desc, default in CONFIG_SCHEMA:
            result[key] = db_vals.get(key, env_vals.get(key, default))
        return result

    def get_category(self, category: str) -> dict[str, str]:
        return {
            k: v for k, v in self.get_all().items() if k.startswith(category + ".")
        }

    def save(self, updates: dict[str, str]) -> int:
        """保存配置项到 system_config 表，并同步写 .env"""
        valid_keys = {k for k, _c, _d, _def in CONFIG_SCHEMA}
        n = 0
        with get_db_session() as session:
            for key, value in updates.items():
                if key not in valid_keys:
                    continue
                session.execute(
                    text(
                        """
                        INSERT INTO system_config (key, value, category, updated_at)
                        VALUES (:key, :value, :cat, NOW())
                        ON CONFLICT (key) DO UPDATE SET
                            value = EXCLUDED.value,
                            updated_at = NOW()
                        """
                    ),
                    {"key": key, "value": str(value), "cat": key.split(".")[0]},
                )
                n += 1
        self._write_env(updates)
        logger.info(f"配置已保存: {n} 项")
        return n

    def _write_env(self, updates: dict[str, str]) -> None:
        """将配置项映射写回 .env（覆盖对应行）"""
        if not self.env_path.exists():
            return
        lines = self.env_path.read_text(encoding="utf-8").splitlines()
        existing = {}
        for line in lines:
            if line.strip() and "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                existing[k.strip()] = line
        for key, value in updates.items():
            env_name = _ENV_MAP.get(key)
            if not env_name:
                continue
            new_line = f"{env_name}={value}"
            if env_name in existing:
                lines[lines.index(existing[env_name])] = new_line
            else:
                lines.append(new_line)
        self.env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def mask(self, value: str) -> str:
        """敏感值打码展示"""
        if not value:
            return ""
        if len(value) <= 8:
            return "****"
        return value[:4] + "****" + value[-4:]

    def describe_all(self) -> list[dict]:
        """返回配置元数据（前端表单渲染用），值已打码"""
        vals = self.get_all()
        out = []
        for key, cat, desc, default in CONFIG_SCHEMA:
            val = vals.get(key, default)
            if _is_secret(key):
                val = self.mask(val)
            item = {
                "key": key,
                "category": cat,
                "description": desc,
                "value": val,
                "default": default,
                "secret": _is_secret(key),
            }
            if key.startswith("llm.") and key.endswith("_model"):
                provider = key.split(".")[1][: -len("_model")]
                item["options"] = MODEL_OPTIONS.get(provider, [])
            out.append(item)
        return out


def os_getenv(name: str) -> str:
    return _os.getenv(name, "")


def fetch_remote_models(provider: str, timeout: int = 15) -> tuple[list[str], str | None]:
    """从已配置的服务器拉取全部可用模型

    返回 (模型id列表, 错误信息)。OpenAI 兼容端点用 /models；
    Ollama 用原生 /api/tags（自动去掉 base_url 的 /v1 后缀）。
    """
    cfg = ConfigStore().get_all()
    base = (cfg.get(f"llm.{provider}_base_url", "") or "").strip().rstrip("/")
    api_key = (cfg.get(f"llm.{provider}_api_key", "") or "").strip()
    if not base:
        return [], "该服务商未配置 Base URL"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        if provider == "ollama":
            root = base[: -len("/v1")] if base.endswith("/v1") else base
            resp = requests.get(f"{root}/api/tags", timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
            return models, None
        resp = requests.get(f"{base}/models", headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        models = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
        return models, None
    except requests.exceptions.HTTPError as e:
        detail = f"HTTP {e.response.status_code}: {e.response.text[:120]}" if e.response is not None else str(e)
        return [], detail
    except Exception as e:
        return [], str(e)


def get_llm_overrides() -> dict:
    """返回 LLM 相关配置覆盖：{provider_name: {api_key, base_url, model}}"""
    overrides = {}
    try:
        cfg = ConfigStore().get_all()
    except Exception:
        return overrides
    default_provider = cfg.get("llm.default_provider", "deepseek")
    for name in ("deepseek", "qwen", "moonshot", "glm", "ollama", "openai"):
        api = cfg.get(f"llm.{name}_api_key", "")
        base = cfg.get(f"llm.{name}_base_url", "")
        model = cfg.get(f"llm.{name}_model", "")
        if api or base or model:
            overrides[name] = {
                "api_key": api,
                "base_url": base,
                "model": model,
            }
    overrides["_default_provider"] = default_provider
    return overrides
