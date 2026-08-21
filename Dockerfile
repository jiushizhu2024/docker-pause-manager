FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libnetfilter-queue1 \
    iptables \
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates/ templates/

EXPOSE 5287

CMD ["python3", "app.py"]
