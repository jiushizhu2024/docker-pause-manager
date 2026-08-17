# Docker Pause Manager

<p align="center">
  <strong>中文</strong> | <a href="#chinese">中文版</a> | <a href="#english">English</a>
</p>

---

<a name="chinese"></a>
## 🇨🇳 中文版

# Docker Pause Manager

基于 AF_PACKET 包嗅探的 Docker 容器自动休眠管理器。当容器在设定时间内没有网络流量时自动 pause 释放资源，有访问时自动唤醒。

## ✨ 项目特点

- **智能休眠**：基于真实网络流量检测，而非端口扫描，准确率高
- **自动唤醒**：检测到访问请求后瞬间 unpause，用户无感知
- **多端口支持**：支持 TCP/UDP 多端口容器（如媒体服务器）
- **Web UI**：简洁易用的管理界面，支持中/英/繁体
- **高亮对比度**：亮色/暗色主题，适合各种使用环境
- **安全认证**：管理员密码保护，防止未授权访问

## 🚀 优势

| 特性 | 说明 |
|------|------|
| **资源节约** | 闲置容器释放 CPU 和内存，降低主机负载 |
| **无需修改容器** | 监听宿主机网络接口，容器本身无需修改 |
| **低开销** | 使用 AF_PACKET 原始套接字，性能优于 iptables |
| **即插即用** | 一行 docker-compose.yml 即可部署 |
| **持久化配置** | 配置保存在 `config.json`，容器重启不丢失 |

## 📋 docker-compose.yml 详解

```yaml
services:
  docker-pause-manager:
    image: ghcr.io/jiushizhu2024/docker-pause-manager:latest  # 镜像地址
    container_name: docker-pause-manager                       # 容器名称
    restart: unless-stopped                                    # 自动重启策略
    network_mode: host                                         # 主机网络（必需：用于 AF_PACKET 嗅探）
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock             # Docker Socket（必需）
    environment:
      - CONFIG_PATH=/app/config.json                          # 配置文件路径
      - STATE_PATH=/app/state.json                            # 状态文件路径
      - LISTEN_HOST=0.0.0.0                                   # 监听地址（0.0.0.0=所有接口）
      - LISTEN_PORT=5287                                      # Web UI 端口
    cap_add:
      - NET_ADMIN                                             # 网络管理权限（必需）
      - NET_RAW                                               # 原始套接字权限（必需）
```

### 网络模式说明

`network_mode: host` 是必需的，原因：
1. **AF_PACKET 包嗅探**：需要监听所有网络流量，host 网络才能捕获宿主机流量
2. **容器直通**：容器可以直接使用宿主机网络接口

> **注意**：host 模式下不能使用 `ports` 映射，端口通过 `LISTEN_PORT` 环境变量配置。

## 🛠️ 部署步骤

### 方法一：直接复制 docker-compose.yml（推荐）

```bash
# 1. 创建目录
mkdir docker-pause-manager && cd docker-pause-manager

# 2. 创建 docker-compose.yml 文件
cat > docker-compose.yml << 'EOF'
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
EOF

# 3. 启动容器
docker compose up -d

# 4. 访问 Web UI
# http://<服务器IP>:5287
# 默认密码: admin123
```

### 方法二：克隆仓库

```bash
git clone https://github.com/jiushizhu2024/docker-pause-manager.git
cd docker-pause-manager
docker compose up -d
```

### 方法三：使用 Docker 命令

```bash
docker run -d \
  --name docker-pause-manager \
  --restart unless-stopped \
  --network host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e LISTEN_HOST=0.0.0.0 \
  -e LISTEN_PORT=5287 \
  --cap-add NET_ADMIN \
  --cap-add NET_RAW \
  ghcr.io/jiushizhu2024/docker-pause-manager:latest
```

## ⚙️ 配置说明

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LISTEN_HOST` | 监听地址 | `0.0.0.0` |
| `LISTEN_PORT` | Web UI 端口 | `5287` |
| `CONFIG_PATH` | 配置文件路径 | `/app/config.json` |
| `STATE_PATH` | 状态文件路径 | `/app/state.json` |

### 修改端口

```yaml
environment:
  - LISTEN_PORT=8080  # 改为 8080
```

## 📁 文件结构

```
docker-pause-manager/
├── docker-compose.yml     # Docker Compose 配置
├── Dockerfile             # 镜像构建文件
├── app.py                 # 主应用程序
├── templates/
│   └── index.html         # Web UI 界面
└── README.md              # 说明文档
```

## 🔧 常用命令

```bash
# 启动
docker compose up -d

# 查看日志
docker logs -f docker-pause-manager

# 停止
docker compose stop

# 重启
docker compose restart

# 更新
docker compose pull && docker compose up -d
```

## 🔐 安全建议

1. **修改默认密码**：首次登录后立即修改密码
2. **限制访问**：使用防火墙限制只有内网可访问 Web UI
3. **不暴露端口**：`LISTEN_HOST` 默认 `0.0.0.0`，如需限制可改为 `127.0.0.1`

## 📝 工作流程

1. 启动后监听指定网络接口的所有流量
2. 轮询所有运行中的容器，检测端口流量
3. 当容器空闲超过设定时间，自动 pause
4. 检测到访问请求时，自动 unpause
5. 所有配置保存到 `config.json`，重启后自动加载

---

<a name="english"></a>
## 🇬🇧 English Version

# Docker Pause Manager

Docker container auto-sleep manager based on AF_PACKET packet sniffing. Automatically pauses containers with no network traffic for a set time, and wakes them on demand.

## ✨ Features

- **Smart Sleep Detection**: Based on real network traffic analysis, high accuracy
- **Instant Wake-up**: Automatically unpause when access is detected, transparent to users
- **Multi-port Support**: Supports containers with multiple TCP/UDP ports
- **Web UI**: Clean and intuitive management interface, supports CN/EN/Traditional Chinese
- **High Contrast UI**: Light/dark themes for various environments
- **Secure Authentication**: Admin password protection

## 🚀 Advantages

| Feature | Description |
|---------|-------------|
| **Resource Saving** | Idle containers release CPU and memory, reducing host load |
| **No Container Modification** | Listens on host network interfaces, containers unchanged |
| **Low Overhead** | Uses AF_PACKET raw sockets, better performance than iptables |
| **Plug & Play** | One-line docker-compose.yml to deploy |
| **Persistent Config** | Config saved to `config.json`, survives container restart |

## 📋 docker-compose.yml Explained

```yaml
services:
  docker-pause-manager:
    image: ghcr.io/jiushizhu2024/docker-pause-manager:latest  # Image source
    container_name: docker-pause-manager                       # Container name
    restart: unless-stopped                                    # Auto-restart policy
    network_mode: host                                         # Host network (required for AF_PACKET)
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock             # Docker Socket (required)
    environment:
      - CONFIG_PATH=/app/config.json                          # Config file path
      - STATE_PATH=/app/state.json                            # State file path
      - LISTEN_HOST=0.0.0.0                                   # Listen address (0.0.0.0=all interfaces)
      - LISTEN_PORT=5287                                      # Web UI port
    cap_add:
      - NET_ADMIN                                             # Network admin capability (required)
      - NET_RAW                                               # Raw socket capability (required)
```

### Network Mode Explanation

`network_mode: host` is required because:
1. **AF_PACKET Sniffing**: Need to monitor all network traffic, host network can capture host traffic
2. **Container Pass-through**: Container can directly use host network interfaces

> **Note**: Cannot use `ports` mapping in host mode. Port is configured via `LISTEN_PORT` environment variable.

## 🛠️ Deployment Steps

### Method 1: Copy docker-compose.yml (Recommended)

```bash
# 1. Create directory
mkdir docker-pause-manager && cd docker-pause-manager

# 2. Create docker-compose.yml
cat > docker-compose.yml << 'EOF'
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
EOF

# 3. Start container
docker compose up -d

# 4. Access Web UI
# http://<server-ip>:5287
# Default password: admin123
```

### Method 2: Clone Repository

```bash
git clone https://github.com/jiushizhu2024/docker-pause-manager.git
cd docker-pause-manager
docker compose up -d
```

### Method 3: Docker Run Command

```bash
docker run -d \
  --name docker-pause-manager \
  --restart unless-stopped \
  --network host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e LISTEN_HOST=0.0.0.0 \
  -e LISTEN_PORT=5287 \
  --cap-add NET_ADMIN \
  --cap-add NET_RAW \
  ghcr.io/jiushizhu2024/docker-pause-manager:latest
```

## ⚙️ Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LISTEN_HOST` | Listen address | `0.0.0.0` |
| `LISTEN_PORT` | Web UI port | `5287` |
| `CONFIG_PATH` | Config file path | `/app/config.json` |
| `STATE_PATH` | State file path | `/app/state.json` |

### Change Port

```yaml
environment:
  - LISTEN_PORT=8080  # Change to 8080
```

## 📁 File Structure

```
docker-pause-manager/
├── docker-compose.yml     # Docker Compose config
├── Dockerfile             # Image build file
├── app.py                 # Main application
├── templates/
│   └── index.html         # Web UI interface
└── README.md              # Documentation
```

## 🔧 Common Commands

```bash
# Start
docker compose up -d

# View logs
docker logs -f docker-pause-manager

# Stop
docker compose stop

# Restart
docker compose restart

# Update
docker compose pull && docker compose up -d
```

## 🔐 Security Tips

1. **Change Default Password**: Change password immediately after first login
2. **Limit Access**: Use firewall to restrict Web UI to internal network only
3. **Bind to Local**: Set `LISTEN_HOST` to `127.0.0.1` to restrict to localhost only

## 📝 How It Works

1. On startup, monitor all traffic on specified network interfaces
2. Poll all running containers, detect port traffic
3. Auto-pause containers idle beyond the set time
4. Auto-unpause when access requests are detected
5. All configs saved to `config.json`, auto-loaded on restart

---

## 🤝 Contributing

Pull requests welcome!

## 📄 License

MIT License
