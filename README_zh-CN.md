# Docker Pause Manager

基于连接活动自动休眠/唤醒 Docker 容器。空闲时休眠，访问时自动唤醒。

[English Docs](README.md)

## 功能特性

- **自动休眠**: 空闲超时后自动暂停容器
- **自动唤醒**: 检测到连接时自动恢复容器
- **多端口监控**: 支持单个容器监控多个 TCP/UDP 端口
- **多语言支持**: 中文 (zh-CN) 和英文 (en-US)
- **轻量级**: 基于 Alpine 的 Docker 镜像，体积优化

## 快速开始

### Docker Compose（推荐）

```bash
git clone https://github.com/jiushizhu2024/docker-pause-manager.git
cd docker-pause-manager
docker-compose up -d
```

### 手动 Docker 运行

```bash
docker run -d \
  --name docker-pause-manager \
  -p 5287:5287 \
  -v /path/to/config.json:/app/config.json:ro \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -e JWT_SECRET=your-secret-key \
  --cap-add NET_ADMIN \
  --cap-add NET_RAW \
  ghcr.io/jiushizhu2024/docker-pause-manager:latest
```

## 配置说明

创建 `config.json`:

```json
{
  "watchers": [
    {
      "container": "myapp",
      "host_ports": [
        {"port": 8080, "protocol": "tcp"},
        {"port": 53, "protocol": "udp"}
      ],
      "idle_seconds": 300,
      "enabled": true
    }
  ],
  "settings": {
    "check_interval": 2,
    "listen_port": 5287
  }
}
```

## API 接口

- `GET /api/watchers` - 查看所有监控器
- `POST /api/watchers` - 添加监控器
- `PUT /api/watchers/<name>` - 更新监控器
- `DELETE /api/watchers/<name>` - 删除监控器
- `POST /api/watchers/<name>/pause` - 手动暂停
- `POST /api/watchers/<name>/unpause` - 手动唤醒
- `POST /api/watchers/<name>/start` - 手动启动（已停止的容器）
- `GET /api/containers` - 列出 Docker 容器
- `GET /api/settings` - 获取设置
- `POST /api/settings` - 更新设置
- `GET /api/i18n/<lang>` - 获取翻译
- `GET /api/languages` - 列出支持的语言

## 多语言支持

- 中文 (zh-CN)
- 英文 (en-US)

通过 URL 切换：`?lang=en-US` 或通过 UI 语言选择器。

## 安全性

- 容器内以非 root 用户运行
- 最小化基础镜像（Alpine）
- 配置文件只读挂载
- Docker socket 只读挂载
- JWT 管理员认证

## 许可证

MIT
