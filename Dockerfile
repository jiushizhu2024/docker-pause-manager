FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    conntrack iptables && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir flask requests-unixsocket2

COPY app.py .
COPY templates/ templates/

ENV LISTEN_HOST=0.0.0.0
ENV LISTEN_PORT=5287

EXPOSE 5287

CMD ["python3", "app.py"]
