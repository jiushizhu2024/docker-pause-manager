FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    conntrack \
    iptables \
    --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY app.py /app/app.py

# 使用 requests-unixsocket2 直接调 Docker API，绕过 docker SDK 的兼容问题
RUN pip install --no-cache-dir \
    flask \
    requests \
    requests-unixsocket2 \
    pyjwt

EXPOSE 5287

CMD ["python3", "/app/app.py"]