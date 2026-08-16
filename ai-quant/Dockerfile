FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 TZ=Asia/Shanghai
RUN apt-get update && apt-get install -y build-essential gcc g++ curl wget libpq-dev && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m -u 1000 quant && chown -R quant:quant /app
USER quant
EXPOSE 8000
CMD ["python", "main.py", "--run"]
