# User Instruction Memory

This file records user instructions, preferences, and teachings for reference in future interactions.

## Entries

[Project Knowledge Summary]
- Date: 2026-08-16
- Context: Discovered by Agent while building AI 自主进化选股系统 (ai-quant) 数据管线与 LLM 进化流程
- Category: Troubleshooting & Debugging / Environment Configuration
- Instructions:
  - Tushare 限频：daily_basic 1次/分钟、trade_cal 1次/小时、daily 与 daily_basic 按 ts_code 单股查询正常。daily_basic 无法全量回填，采用"最近 3 个交易日真实值 + 其余日期合成估值填充"策略（_backfill_daily_basic）。
  - Akshare 单股接口在沙箱环境常挂起（RemoteDisconnected 或网络黑洞无响应）。_call_with_fallback 必须用 ThreadPoolExecutor + future.result(timeout=25) 包裹；严禁用 `with ThreadPoolExecutor` 块，其 __exit__ 的 shutdown(wait=True) 会等待挂起线程导致进程卡死，须用 ex.shutdown(wait=False)。
  - LLM prompt 模板内包含 JSON 示例花括号时，不能用 str.format()（会 KeyError），必须用 replace 按占位符替换（generator.py 的 _safe_format）。
  - src/factor/engine.py 依赖 _load_history（返回空 DataFrame 即可）与 _to_float（Decimal 列转 float）两个方法，缺失会 AttributeError。
  - 因子快照按日计算 800 只（按成交额排序），技术因子 rolling 在单日快照内跨股票行计算（近似，非逐股序列）。

[Project Knowledge Summary]
- Date: 2026-08-16
- Context: Discovered by Agent while接入 FTShare（非凸科技）免费数据源
- Category: Environment Configuration / Troubleshooting & Debugging
- Instructions:
  - FTShare 公共 MCP 端点 `https://market.ft.tech/gateway/mcp` 免费无需 token，JSON-RPC over HTTP（streamable HTTP），requests 即可调用（curl HTTP/2 POST 间歇失败，用 requests）。
  - SSE 响应解析坑：JSON 被服务端拆成多行仅首行带 `data: ` 前缀，且 `id:`/`retry:`/`event:` 是元数据行必须忽略，按空行事件边界拼接（_parse_sse）；空响应体返回 {}（notifications/initialized）。
  - 关键工具：`ft_daec_ohlcs`（A股历史日线，symbol 用 `600519.XSHG` 格式，since/until 格式 YYYYMMDD）；`ft_get_eastmoney_stock_valuation`（个股历史估值 pe_ttm/pb_mrq，不带日期或翻页可取全量，total 约 2091 条/8年）；`ft_daec_stocks_all`（全市场快照含 pe_ttm，但每页固定返回 36 条，上限 page_size=200，仅当日实时无历史）；`daily_ohlc`（东财上游）A股被拒不可用。
  - 估值回填脚本：`python3 scripts/backfill_ftshare_valuation.py`（TOP60 只近 1 年真实 pe/pb，覆盖合成数据，约 4 分钟）。

[Project Knowledge Summary]
- Date: 2026-08-16
- Context: Discovered by Agent while running ai-quant 构建与验证
- Category: Build Methods
- Instructions:
  - 数据回填：`python3 scripts/backfill_data.py --years 1 --codes "600519.SH,..."`（先 Akshare 失败自动切 Tushare，回填后自动清理节假日残留）
  - 因子重算：`python3 -c "import asyncio; from src.factor.engine import FactorEngine; asyncio.run(FactorEngine().calculate_history(days=120))"`
  - 模拟回放：`python3 main.py --simulate 60`；Web：`python3 main.py --web`（0.0.0.0:8000，admin/admin123，Basic 认证，仅 /api/health 免认证）
  - 进化触发：`POST /api/evolution/trigger`（DeepSeek 变异/杂交，约 60s；LLM 失败自动降级内置模板）
  - 竞技场容量 20：淘汰末位后补新生，容量满时新策略被拒
