# AI 自主进化选股系统

基于大语言模型(LLM)的 A 股量化选股策略自动生成与进化系统。

> ⚠️ **重要声明**：本系统仅供学习研究使用，不构成任何投资建议。量化交易有风险，实盘使用请谨慎评估。

## 核心思想

1. **LLM 生成策略**：利用 DeepSeek / 通义千问 / Kimi / GLM 等国内大模型自动生成选股策略
2. **竞技场验证**：多个策略在模拟环境中竞争，优胜劣汰
3. **达尔文进化**：精英策略变异、杂交，持续进化
4. **知识库增强**：RAG 系统注入金融学术知识

## 系统架构

```
┌──────────────────────────────────────────────┐
│               Web Dashboard (FastAPI+Plotly)  │
├──────────────────────────────────────────────┤
│  LLM策略生成器 → 策略编译器 → 轻量验证层       │
│        ↑              ↓            ↓         │
│  知识库(RAG)  ←  模拟竞技场(20策略)          │
│                          ↓                   │
│              进化引擎(每周:淘汰→变异→杂交)    │
├──────────────────────────────────────────────┤
│  数据管道 │ 因子计算 │ 监控告警 │ 调度系统     │
├──────────────────────────────────────────────┤
│     PostgreSQL + Redis + ChromaDB            │
└──────────────────────────────────────────────┘
```

## 快速开始

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 LLM Key 与 TUSHARE_TOKEN
```

支持的 LLM 服务商（均可选填）：

| 服务商 | 环境变量 | 模型示例 |
|--------|---------|---------|
| DeepSeek | `DEEPSEEK_API_KEY` | deepseek-chat |
| 通义千问 | `QWEN_API_KEY` | qwen-plus |
| Moonshot Kimi | `MOONSHOT_API_KEY` | moonshot-v1-8k |
| 智谱 GLM | `GLM_API_KEY` | glm-4 |
| 百度千帆 | `BAIDU_API_KEY` | ernie-4.0-8k |
| 本地 Ollama | `OLLAMA_BASE_URL` | qwen2.5:14b |

通过 `DEFAULT_LLM_PROVIDER` 切换默认服务商。未配置 Key 时自动降级为内置策略模板。

数据源：`TUSHARE_TOKEN`（付费）+ Akshare（免费）双数据源，通过 `PRIMARY_DATA_PROVIDER` 切换。

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 初始化数据库（需 PostgreSQL + Redis）

```bash
python main.py --init
```

### 4. 回填历史数据（可选，推荐）

```bash
python main.py --backfill 1   # 回填1年
# 或按代码回填
python scripts/backfill_data.py --years 1 --codes 000001.SZ,600000.SH
```

### 5. 启动

```bash
# 全量运行（调度器 + Web）
python main.py --run

# 仅 Web
python main.py --web

# 手动触发进化
python main.py --evolve

# 回放最近 N 个交易日模拟交易
python main.py --simulate 90
```

访问 Web 面板：`http://localhost:8000`，默认账号 `admin / admin123`。

## Docker 部署

```bash
make build && make up
make init
make backfill YEARS=1
# 访问 http://服务器IP:8000
```

## 目录结构

```
ai-quant/
├── main.py                    # 系统入口
├── config/                    # 配置（settings / factor_registry）
├── src/
│   ├── core/                  # 配置/数据库/缓存
│   ├── data/                  # 数据管道（Tushare + Akshare）
│   ├── factor/                # 因子计算引擎
│   ├── knowledge/             # 金融知识库 RAG（ChromaDB）
│   ├── llm/                   # 多模型 LLM 客户端
│   ├── strategy/              # 策略生成/编译/验证
│   ├── arena/                 # 模拟竞技场
│   ├── evolution/             # 进化引擎
│   ├── monitor/               # 监控告警
│   ├── scheduler/             # 定时任务
│   └── web/                   # FastAPI Web 面板
└── scripts/                   # 运维脚本
```

## 定时任务

| 时间 | 任务 |
|------|------|
| 工作日 15:30 | 每日数据更新 |
| 工作日 16:00 | 每日模拟交易 |
| 工作日 16:30 | 每日告警检查 |
| 工作日 17:00 | 每日报告 |
| 周六 10:00 | 每周进化 |
| 周六 18:00 | 每周报告 |
| 每小时 | 系统健康检查 |
| 凌晨 3:00 | 数据清理 |
