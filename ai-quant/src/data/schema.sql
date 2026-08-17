-- AI 交易 Agent 系统 - 数据库表结构

CREATE TABLE IF NOT EXISTS stock_basic (
    ts_code VARCHAR(20) PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    name VARCHAR(50) NOT NULL,
    area VARCHAR(20),
    industry VARCHAR(50),
    market VARCHAR(10),
    list_date DATE,
    is_hs BOOLEAN DEFAULT FALSE,
    delisted BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS daily_price (
    id BIGSERIAL PRIMARY KEY,
    ts_code VARCHAR(20) NOT NULL,
    trade_date DATE NOT NULL,
    open DECIMAL(12,3),
    high DECIMAL(12,3),
    low DECIMAL(12,3),
    close DECIMAL(12,3),
    pre_close DECIMAL(12,3),
    change_pct DECIMAL(8,4),
    vol DECIMAL(20,2),
    amount DECIMAL(20,2),
    turnover_rate DECIMAL(8,4),
    pe_ttm DECIMAL(12,3),
    pb DECIMAL(12,3),
    dv_ttm DECIMAL(12,3),
    UNIQUE(ts_code, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_price_date ON daily_price(trade_date);
CREATE INDEX IF NOT EXISTS idx_daily_price_code ON daily_price(ts_code);

-- 交易 Agent
CREATE TABLE IF NOT EXISTS agent (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT DEFAULT '',
    system_prompt TEXT DEFAULT '',
    llm_provider VARCHAR(30) DEFAULT 'deepseek',
    llm_api_key TEXT DEFAULT '',
    llm_base_url TEXT DEFAULT '',
    llm_model VARCHAR(100) DEFAULT '',
    status VARCHAR(20) DEFAULT 'running',          -- running/paused/archived
    is_overseer BOOLEAN DEFAULT FALSE,             -- 统筹 Agent（总管）
    skills TEXT DEFAULT '',                        -- 逗号分隔的技能集
    initial_capital DECIMAL(16,2) DEFAULT 100000,
    current_cash DECIMAL(16,2) DEFAULT 100000,
    max_position INTEGER DEFAULT 10,
    single_stock_weight DECIMAL(5,2) DEFAULT 0.10,
    commission_rate DECIMAL(8,4) DEFAULT 0.0003,
    slippage DECIMAL(8,4) DEFAULT 0.001,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    last_active_at TIMESTAMP
);

-- Agent 长期记忆
CREATE TABLE IF NOT EXISTS agent_memory (
    id BIGSERIAL PRIMARY KEY,
    agent_id VARCHAR(64) NOT NULL,
    content TEXT NOT NULL,
    memory_type VARCHAR(20) DEFAULT 'experience',  -- instruction/experience/chat_summary
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_memory ON agent_memory(agent_id);

-- Agent 对话记录
CREATE TABLE IF NOT EXISTS agent_chat (
    id BIGSERIAL PRIMARY KEY,
    agent_id VARCHAR(64) NOT NULL,
    role VARCHAR(10) NOT NULL,                     -- user/assistant
    content TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_chat ON agent_chat(agent_id, created_at);

-- Agent 模拟持仓
CREATE TABLE IF NOT EXISTS agent_position (
    id BIGSERIAL PRIMARY KEY,
    agent_id VARCHAR(64) NOT NULL,
    ts_code VARCHAR(20) NOT NULL,
    shares INTEGER NOT NULL,
    avg_cost DECIMAL(16,4) NOT NULL,
    current_price DECIMAL(12,3),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(agent_id, ts_code)
);

-- Agent 模拟成交
CREATE TABLE IF NOT EXISTS agent_trade (
    id BIGSERIAL PRIMARY KEY,
    agent_id VARCHAR(64) NOT NULL,
    ts_code VARCHAR(20) NOT NULL,
    direction VARCHAR(10) NOT NULL,                -- buy/sell
    price DECIMAL(12,3) NOT NULL,
    shares INTEGER NOT NULL,
    trade_date DATE NOT NULL,
    reason TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_trade ON agent_trade(agent_id, trade_date);

-- Agent 每日绩效
CREATE TABLE IF NOT EXISTS agent_performance (
    id BIGSERIAL PRIMARY KEY,
    agent_id VARCHAR(64) NOT NULL,
    trade_date DATE NOT NULL,
    nav DOUBLE PRECISION,
    daily_return DOUBLE PRECISION,
    cumulative_return DOUBLE PRECISION,
    positions_count INTEGER,
    cash DOUBLE PRECISION,
    total_value DOUBLE PRECISION,
    UNIQUE(agent_id, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_agent_perf_date ON agent_performance(trade_date);

-- Agent 定时自动任务
CREATE TABLE IF NOT EXISTS agent_task (
    id BIGSERIAL PRIMARY KEY,
    agent_id VARCHAR(64) NOT NULL,
    schedule_type VARCHAR(20) NOT NULL,            -- daily/interval
    schedule_time VARCHAR(10) DEFAULT '',          -- HH:MM (daily)
    interval_hours DOUBLE PRECISION DEFAULT 0,     -- hours (interval)
    enabled BOOLEAN DEFAULT TRUE,
    last_run_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_task ON agent_task(agent_id);

CREATE TABLE IF NOT EXISTS system_logs (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT NOW(),
    level VARCHAR(10) NOT NULL,
    module VARCHAR(100),
    message TEXT,
    details JSONB
);

CREATE TABLE IF NOT EXISTS system_config (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT,
    category VARCHAR(50) DEFAULT 'system',
    description TEXT,
    updated_at TIMESTAMP DEFAULT NOW()
);
