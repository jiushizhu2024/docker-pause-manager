# Dockerfile for Docker Pause Manager
# Multi-stage build with Alpine base for minimal image size

# Stage 1: Build stage
FROM python:3.11-alpine AS builder

WORKDIR /build

# Install build dependencies
RUN apk add --no-cache git

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: Production stage
FROM python:3.11-alpine AS production

# Add non-root user
RUN addgroup -g 1000 -S appgroup && \
    adduser -u 1000 -S appuser -G appgroup

# Set working directory
WORKDIR /app

# Copy installed dependencies from builder
COPY --from=builder /root/.local /home/appuser/.local

# Ensure ~/.local/bin is in PATH
ENV PATH=/home/appuser/.local/bin:$PATH

# Copy application files
COPY app.py .
COPY static/ static/
COPY i18n/ i18n/

# 如果存在 config.json 则复制，否则使用示例配置
COPY config.json.example config.json

# Change ownership to appuser
RUN chown -R appuser:appgroup /app

# Install wget for healthcheck (需要 root 权限，必须在 USER 之前)
RUN apk add --no-cache wget

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 5287

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:5287/api/watchers || exit 1

# Docker socket 使用 Unix socket，需要设置环境变量
ENV DOCKER_HOST=unix:///var/run/docker.sock
ENV DOCKER_CONTEXT=default

# Start application
CMD ["python3", "app.py"]
