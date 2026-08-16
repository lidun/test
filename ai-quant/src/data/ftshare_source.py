"""FTShare（非凸科技）免费数据源实现

通过公共 MCP 端点（https://market.ft.tech/gateway/mcp）调用：
- ft_daec_ohlcs: A 股历史日线 OHLC
- ft_daec_stocks_all: 全市场实时快照（含 pe_ttm 等基本面字段）
- ft_get_eastmoney_stock_valuation: 个股历史估值（pe_ttm/pb），翻页可取全量
"""
from __future__ import annotations

import json
import threading
from datetime import date

import pandas as pd
from loguru import logger
import requests

from src.data.base_provider import DataProvider

MCP_ENDPOINT = "https://market.ft.tech/gateway/mcp"
PROTOCOL_VERSION = "2025-03-26"
TOOL_TIMEOUT = 60


def _parse_sse(text: str) -> dict:
    """解析 streamable HTTP 的 SSE 响应。

    服务端将 JSON 拆成多行，仅首行带 `data: ` 前缀；`id:`/`retry:`/`event:`
    为元数据字段需忽略，按空行事件边界拼接 data 内容。
    """
    events = []
    current = []
    for line in text.splitlines():
        if line == "":
            if current:
                events.append("".join(current))
                current = []
        elif line.startswith(("id:", "retry:", "event:")):
            continue
        elif line.startswith("data: "):
            current.append(line[6:])
        elif line == "data:":
            current.append("")
        else:
            current.append(line)
    if current:
        events.append("".join(current))
    for ev in events:
        if not ev.strip():
            continue
        try:
            return json.loads(ev)
        except json.JSONDecodeError:
            continue
    raise ValueError("SSE 响应中没有有效的 JSON 事件")


class _MCPClient:
    """极简 MCP streamable HTTP 客户端"""

    def __init__(self, endpoint: str = MCP_ENDPOINT):
        self.endpoint = endpoint
        self.session_id = None
        self._lock = threading.Lock()
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

    def _post(self, payload: dict, timeout: float = TOOL_TIMEOUT) -> dict:
        headers = dict(self._headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
            headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
        resp = requests.post(self.endpoint, headers=headers, data=json.dumps(payload), timeout=timeout)
        resp.encoding = "utf-8"
        if not self.session_id and "Mcp-Session-Id" in resp.headers:
            self.session_id = resp.headers["Mcp-Session-Id"]
        if not resp.text.strip():
            return {}
        return _parse_sse(resp.text)

    def initialize(self) -> bool:
        with self._lock:
            try:
                self._post(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": PROTOCOL_VERSION,
                            "capabilities": {},
                            "clientInfo": {"name": "ai-quant", "version": "1"},
                        },
                    }
                )
                self._post(
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    timeout=15,
                )
                return True
            except Exception as e:
                logger.warning(f"FTShare MCP 初始化失败: {e}")
                return False

    def call_tool(self, name: str, arguments: dict) -> dict:
        with self._lock:
            if not self.session_id:
                self.initialize()
            payload = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
            resp = self._post(payload)
            result = resp.get("result", {})
            text = ""
            for item in result.get("content", []):
                if item.get("type") == "text":
                    text += item.get("text", "")
            if result.get("isError"):
                raise RuntimeError(f"FTShare {name} 上游拒绝: {text[:200]}")
            if text:
                return json.loads(text)
            return result.get("structuredContent", {}) or {}


def _to_ft_symbol(ts_code: str) -> str:
    """600519.SH -> 600519.XSHG，000001.SZ -> 000001.XSHE"""
    code, _, suffix = ts_code.partition(".")
    if suffix.upper() == "SH":
        return f"{code}.XSHG"
    return f"{code}.XSHE"


def _to_ts_code(symbol: str) -> str:
    """000001.XSHE -> 000001.SZ，600519.XSHG -> 600519.SH"""
    code, _, suffix = symbol.partition(".")
    if suffix.upper() == "XSHG":
        return f"{code}.SH"
    return f"{code}.SZ"


class FTShareDataProvider(DataProvider):
    name = "ftshare"

    def __init__(self):
        self._client = _MCPClient()
        self._available = False
        try:
            self._available = self._client.initialize()
        except Exception as e:
            logger.error(f"FTShare 初始化失败: {e}")

    @property
    def available(self) -> bool:
        return self._available

    def _call(self, name: str, **kwargs) -> pd.DataFrame:
        if not self._available:
            return pd.DataFrame()
        try:
            result = self._client.call_tool(name, kwargs)
        except Exception as e:
            logger.warning(f"FTShare {name} 调用失败: {e}")
            return pd.DataFrame()
        data = result.get("data", []) or []
        if not data:
            return pd.DataFrame()
        return pd.DataFrame(data)

    def fetch_stock_basic(self) -> pd.DataFrame:
        frames = []
        for page in range(1, 16):
            df = self._call("ft_daec_stocks_all", page=page, page_size=200)
            if df.empty:
                break
            frames.append(df)
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        df = df[df.get("status", pd.Series(dtype=str)).astype(str) == "Normal"]
        df["ts_code"] = df["symbol"].apply(
            lambda s: _to_ts_code(s) if "." in str(s) else ""
        )
        if "symbol_id" in df.columns:
            df["symbol"] = df["symbol_id"]
        keep = ["ts_code", "symbol", "name", "listing_date"]
        df = df[[c for c in keep if c in df.columns]].rename(
            columns={"listing_date": "list_date"}
        )
        return df

    def fetch_daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """获取单只 A 股历史日线。日期格式 20230101"""
        if not self._available:
            return pd.DataFrame()
        df = self._call(
            "ft_daec_ohlcs",
            symbol=_to_ft_symbol(ts_code),
            since=start_date,
            until=end_date,
        )
        if df.empty:
            return pd.DataFrame()
        df["trade_date"] = pd.to_datetime(df["close_ts_ms"], unit="ms").dt.date
        df["ts_code"] = ts_code
        df["open"] = pd.to_numeric(df["open"], errors="coerce")
        df["high"] = pd.to_numeric(df["high"], errors="coerce")
        df["low"] = pd.to_numeric(df["low"], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["vol"] = pd.to_numeric(df["volume"], errors="coerce")
        df["amount"] = pd.to_numeric(df.get("turnover"), errors="coerce")
        df["pct_chg"] = df["close"].pct_change() * 100
        df["pre_close"] = df["close"].shift(1)
        df = df.sort_values("trade_date")
        keep = [
            "ts_code",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "pct_chg",
            "vol",
            "amount",
        ]
        return df[keep]

    def fetch_daily_batch(self, trade_date: str) -> pd.DataFrame:
        """全市场行情快照，仅当日可用（历史需逐股回填）"""
        return self._market_snapshot(trade_date)

    def fetch_daily_basic(self, trade_date: str) -> pd.DataFrame:
        """全市场估值（pe_ttm/pb），仅当日快照；历史估值见 fetch_valuation_history"""
        return self._market_snapshot(trade_date)

    def _market_snapshot(self, trade_date: str) -> pd.DataFrame:
        today = date.today().strftime("%Y%m%d")
        if trade_date != today:
            return pd.DataFrame()
        frames = []
        for page in range(1, 16):
            df = self._call("ft_daec_stocks_all", page=page, page_size=200)
            if df.empty:
                break
            frames.append(df)
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        df["ts_code"] = df["symbol"].apply(
            lambda s: _to_ts_code(s) if "." in str(s) else ""
        )
        df["trade_date"] = pd.to_datetime(trade_date, format="%Y%m%d")
        df["open"] = pd.to_numeric(df.get("open"), errors="coerce")
        df["high"] = pd.to_numeric(df.get("high"), errors="coerce")
        df["low"] = pd.to_numeric(df.get("low"), errors="coerce")
        df["close"] = pd.to_numeric(df.get("close"), errors="coerce")
        df["pre_close"] = pd.to_numeric(df.get("prev_close"), errors="coerce")
        df["pct_chg"] = pd.to_numeric(df.get("change_rate"), errors="coerce") * 100
        df["vol"] = pd.to_numeric(df.get("volume"), errors="coerce")
        df["amount"] = pd.to_numeric(df.get("turnover"), errors="coerce")
        df["turnover_rate"] = pd.to_numeric(df.get("turnover_rate"), errors="coerce")
        df["pe_ttm"] = pd.to_numeric(df.get("pe_ttm"), errors="coerce")
        keep = [
            "ts_code",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "pct_chg",
            "vol",
            "amount",
            "turnover_rate",
            "pe_ttm",
        ]
        return df[[c for c in keep if c in df.columns]]

    def fetch_valuation_history(
        self, ts_code: str, max_pages: int | None = None
    ) -> pd.DataFrame:
        """拉取单只 A 股历史估值（pe_ttm/pb）。默认按 total 翻页取尽；
        max_pages 限定只取最近 N 页（如 4 页约覆盖 1 年），用于快速回填。
        """
        frames = []
        page = 1
        total = None
        while page <= (max_pages or 80):
            result = self._client.call_tool(
                "ft_get_eastmoney_stock_valuation",
                {"symbol": ts_code.split(".")[0], "page": page, "page_size": 100},
            )
            data = result.get("data", []) or []
            if not data:
                break
            meta = result.get("metadata", {})
            if total is None:
                total = meta.get("total")
            frames.append(pd.DataFrame(data))
            fetched = len(pd.concat(frames, ignore_index=True))
            if total and fetched >= total:
                break
            if meta.get("pagination", {}).get("has_more") is False:
                break
            page += 1
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        df = df.drop_duplicates(subset=["trade_date"])
        df["ts_code"] = ts_code
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df["pe_ttm"] = pd.to_numeric(df.get("pe_ttm"), errors="coerce")
        df["pb"] = pd.to_numeric(df.get("pb_mrq"), errors="coerce")
        df["dv_ttm"] = pd.to_numeric(df.get("dv_ttm"), errors="coerce")
        return df[["ts_code", "trade_date", "pe_ttm", "pb", "dv_ttm"]]
