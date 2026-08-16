"""缓存管理模块：Redis 封装，含降级保护"""
from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger

from src.core.config import config

try:
    import redis as redis_lib
except ImportError:
    redis_lib = None


class RedisCache:
    def __init__(self):
        self._client = None
        if redis_lib is None:
            logger.warning("redis 库未安装，缓存将退化为内存模式")
            self._memory: dict[str, Any] = {}
            self._memory_ttl: dict[str, float] = {}
            return
        try:
            self._client = redis_lib.from_url(
                config.redis.url,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
            )
            self._client.ping()
            logger.info("Redis 连接成功")
        except Exception as e:
            logger.warning(f"Redis 不可用 ({e})，缓存退化为内存模式")
            self._client = None
            self._memory: dict[str, Any] = {}
            self._memory_ttl: dict[str, float] = {}

    def _use_memory(self) -> bool:
        return self._client is None

    def get(self, key: str) -> Optional[str]:
        if self._use_memory():
            import time

            exp = self._memory_ttl.get(key)
            if exp is None:
                return None
            if exp < time.time():
                self._memory.pop(key, None)
                self._memory_ttl.pop(key, None)
                return None
            return self._memory.get(key)
        try:
            return self._client.get(key)
        except Exception:
            return None

    def setex(self, key: str, ttl: int, value: Any):
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        if self._use_memory():
            import time

            self._memory[key] = value
            self._memory_ttl[key] = time.time() + ttl
            return
        try:
            self._client.setex(key, ttl, value)
        except Exception as e:
            logger.debug(f"Redis setex 失败: {e}")

    def delete(self, key: str):
        if self._use_memory():
            self._memory.pop(key, None)
            self._memory_ttl.pop(key, None)
            return
        try:
            self._client.delete(key)
        except Exception:
            pass

    def ping(self) -> bool:
        if self._use_memory():
            return True
        try:
            return bool(self._client.ping())
        except Exception:
            return False


redis_cache = RedisCache()
