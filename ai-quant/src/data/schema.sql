-- AI 自主进化选股系统 - 数据库表结构

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

CREATE TABLE IF NOT EXISTS factor_data (
    id BIGSERIAL PRIMARY KEY,
    ts_code VARCHAR(20) NOT NULL,
    trade_date DATE NOT NULL,
    factor_name VARCHAR(50) NOT NULL,
    factor_value DOUBLE PRECISION,
    UNIQUE(ts_code, trade_date, factor_name)
);

CREATE INDEX IF NOT EXISTS idx_factor_date_name ON factor_data(trade_date, factor_name);
CREATE INDEX IF NOT EXISTS idx_factor_code ON factor_data(ts_code);

CREATE TABLE IF NOT EXISTS strategies (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    type VARCHAR(50) NOT NULL,
    meta JSONB,
    status VARCHAR(20) DEFAULT 'active',
    generation INTEGER DEFAULT 0,
    parent_id VARCHAR(64),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    eliminated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS strategy_performance (
    id BIGSERIAL PRIMARY KEY,
    strategy_id VARCHAR(64) NOT NULL,
    trade_date DATE NOT NULL,
    nav DOUBLE PRECISION,
    daily_return DOUBLE PRECISION,
    cumulative_return DOUBLE PRECISION,
    positions_count INTEGER,
    cash DOUBLE PRECISION,
    total_value DOUBLE PRECISION,
    UNIQUE(strategy_id, trade_date)
);

CREATE TABLE IF NOT EXISTS evolution_log (
    id BIGSERIAL PRIMARY KEY,
    cycle INTEGER NOT NULL,
    timestamp TIMESTAMP DEFAULT NOW(),
    eliminated_count INTEGER,
    mutated_count INTEGER,
    crossover_count INTEGER,
    new_count INTEGER,
    added_count INTEGER,
    arena_size INTEGER,
    details JSONB
);

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

CREATE TABLE IF NOT EXISTS report_history (
    id BIGSERIAL PRIMARY KEY,
    report_type VARCHAR(20) NOT NULL,
    title VARCHAR(200),
    content TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_report_type ON report_history(report_type, created_at);
