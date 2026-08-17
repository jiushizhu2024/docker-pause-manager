# Docker Pause Manager

自动 pause/unpause Docker 容器，基于 AF_PACKET 包嗅探检测流量，空闲超时 pause。

## 快速开始

```bash
mkdir docker-pause-manager && cd docker-pause-manager
# 复制仓库中的 docker-compose.yml
docker compose up -d
```

## docker-compose.yml

```yaml
services:
  docker-pause-manager:
    image: ghcr.io/jiushizhu2024/docker-pause-manager:latest
    container_name: docker-pause-manager
    restart: unless-stopped
    network_mode: host
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      - CONFIG_PATH=/app/config.json
      - STATE_PATH=/app/state.json
      - LISTEN_HOST=0.0.0.0
      - LISTEN_PORT=5287
    cap_add:
      - NET_ADMIN
      - NET_RAW
```

## 配置说明

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `LISTEN_HOST` | 监听地址 | `0.0.0.0` |
| `LISTEN_PORT` | 监听端口 | `5287` |
| `CONFIG_PATH` | 配置文件路径 | `/app/config.json` |
| `STATE_PATH` | 状态文件路径 | `/app/state.json` |

## 访问

Web UI: `http://<IP>:5287`

默认密码: `admin123`

## 功能

- 基于 AF_PACKET 包嗅探检测容器流量
- 空闲超时后自动 pause 容器释放资源
- 访问时自动 unpause 唤醒容器
- 支持多端口容器
- Web UI 管理界面

## 修改密码

在 Web UI 设置页面可以修改管理密码。
