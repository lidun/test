# AI Quant 部署指南（2 核 2G 云服务器）

## 内存预算（总计 2G）

| 组件 | 内存 | 说明 |
|------|------|------|
| Python 主进程 | ~300-700M | systemd `MemoryMax=700M` 硬限制 |
| PostgreSQL | ~200-300M | 按 `postgresql.conf.2g` 调优 |
| Redis | ~130M | `maxmemory 128mb` |
| 系统 + 余量 | ~400-500M | 保障不 OOM |

## 一、安装依赖

```bash
# Debian/Ubuntu
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql redis-server python3-venv git

# 准备虚拟环境
cd /opt
sudo git clone <你的仓库地址> ai-quant
cd ai-quant
sudo python3 -m venv venv
sudo ./venv/bin/pip install -U pip
sudo ./venv/bin/pip install -r requirements.txt
```

## 二、配置数据库

```bash
# 1. PostgreSQL 内存调优（追加配置后重启）
sudo cp deploy/postgresql.conf.2g /etc/postgresql/15/main/conf.d/2g.conf
sudo systemctl restart postgresql

# 2. Redis 内存限制
echo -e "maxmemory 128mb\nmaxmemory-policy allkeys-lru" | sudo tee -a /etc/redis/redis.conf
sudo systemctl restart redis-server

# 3. 创建数据库与账号（按 .env 中的 DATABASE_URL 配置）
sudo -u postgres psql -c "CREATE USER ai_quant WITH PASSWORD 'your_password';"
sudo -u postgres psql -c "CREATE DATABASE ai_quant OWNER ai_quant;"
```

## 三、配置 .env

复制 `.env.example` 为 `.env`，填写数据库连接与大模型 Key：

```env
DATABASE_URL=postgresql+psycopg2://ai_quant:your_password@127.0.0.1:5432/ai_quant
REDIS_URL=redis://127.0.0.1:6379/0
DEEPSEEK_API_KEY=sk-xxxx
```

## 四、安装 systemd 服务

```bash
sudo cp deploy/ai-quant.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ai-quant
sudo systemctl start ai-quant
sudo systemctl status ai-quant
```

查看日志：`sudo journalctl -u ai-quant -f`

## 五、内存相关配置项（Web 界面「系统配置」）

- `evolution.replay_days`：启动回放交易日数，默认 30，调小可降低启动峰值内存
- `system.health_check_interval`：健康检查间隔
- LLM 的 `request_timeout`：请求超时

## 六、OOM 排查

```bash
# 查看 OOM 记录
dmesg | grep -i oom

# 查看当前内存占用
systemctl status ai-quant
ps aux --sort=-rss | head -10

# 若 Python 进程频繁被杀，调低 MemoryHigh 或减少 replay_days
```

## 说明

- 单进程运行（uvicorn 单 worker），已在代码层做内存适配：
  - 策略历史数据有界（nav_history 500 条 / trades 1000 条）
  - benchmark 结果缓存
  - SQLAlchemy 连接池 3+2
  - 启动回放与因子批量计算天数可配置
- 数据库自动清理：每日凌晨 3 点删除 2 年前行情
