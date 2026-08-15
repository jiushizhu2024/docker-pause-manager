# Dockerfile for Docker Pause Manager
# Multi-stage build with Alpine base for minimal image size

# Stage 1: Build stage
FROM python:3.11-alpine AS builder

WORKDIR /build

# Install build dependencies
RUN apk add --no-cache git

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stage 2: Production stage
FROM python:3.11-alpine AS production

# Set working directory
WORKDIR /app

# Copy installed dependencies from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application files
COPY app.py .
COPY static/ static/
COPY i18n/ i18n/

# 如果存在 config.json 则复制，否则使用示例配置
COPY config.json.example config.json

# Install wget for healthcheck
RUN apk add --no-cache wget

# Expose port
EXPOSE 5287

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:5287/api/watchers || exit 1

# Start application
CMD ["python3", "app.py"]
