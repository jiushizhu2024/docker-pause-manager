FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    conntrack \
    iptables \
    --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY app.py /app/app.py

# Pin requests to avoid urllib3 scheme compatibility issue with docker SDK 6.x
RUN pip install --no-cache-dir \
    flask \
    requests==2.31.0 \
    urllib3==1.26.20 \
    docker==6.1.3 \
    pyjwt

EXPOSE 5287

CMD ["python3", "/app/app.py"]