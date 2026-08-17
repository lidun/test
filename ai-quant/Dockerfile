# 国内镜像加速前缀（默认 DaoCloud，可替换为本地可用的其他源）
ARG REGISTRY_PREFIX=docker.m.daocloud.io/library
FROM ${REGISTRY_PREFIX}/python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 TZ=Asia/Shanghai
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null || true; \
    apt-get update && apt-get install -y build-essential gcc g++ curl wget libpq-dev && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
COPY . .
RUN useradd -m -u 1000 quant && chown -R quant:quant /app
USER quant
EXPOSE 8000
CMD ["python", "main.py", "--run"]
