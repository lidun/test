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
- Context: Discovered by Agent while running ai-quant 构建与验证
- Category: Build Methods
- Instructions:
  - 数据回填：`python3 scripts/backfill_data.py --years 1 --codes "600519.SH,..."`（先 Akshare 失败自动切 Tushare，回填后自动清理节假日残留）
  - 因子重算：`python3 -c "import asyncio; from src.factor.engine import FactorEngine; asyncio.run(FactorEngine().calculate_history(days=120))"`
  - 模拟回放：`python3 main.py --simulate 60`；Web：`python3 main.py --web`（0.0.0.0:8000，admin/admin123，Basic 认证，仅 /api/health 免认证）
  - 进化触发：`POST /api/evolution/trigger`（DeepSeek 变异/杂交，约 60s；LLM 失败自动降级内置模板）
  - 竞技场容量 20：淘汰末位后补新生，容量满时新策略被拒
