FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y conntrack iptables && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
RUN pip install --no-cache-dir flask requests-unixsocket2

# 复制应用代码
COPY app.py .

# 暴露端口
EXPOSE 5287

# 运行应用
CMD ["python3", "app.py"]