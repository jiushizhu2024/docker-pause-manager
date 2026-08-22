# Docker Pause Manager

<p align="center">
  <strong>中文</strong> | <a href="#chinese">简体</a> | <a href="#traditional">繁體</a> | <a href="#english">English</a>
</p>

---

<a name="chinese"></a>
## 🇨🇳 简体中文版

# Docker Pause Manager

基于 AF_PACKET 包嗅探的 Docker 容器自动休眠管理器。当容器在设定时间内没有网络流量时自动休眠释放资源，有访问时自动唤醒。支持 **Pause 模式**（释放 CPU，保留内存）和 **Stop 模式**（释放全部资源包括内存）。

## ✨ 项目特点

- **双模式休眠**：支持 Pause（释放 CPU，保留内存）和 Stop（释放全部资源）两种模式，按容器单独配置
- **智能休眠**：基于真实网络流量检测（AF_PACKET + iptables 双重检测），准确率高
- **自动唤醒**：检测到访问请求后瞬间唤醒，用户无感知
- **Stop 模式无缝唤醒**：通过 iptables DROP 规则拦截 SYN 包，使客户端 TCP 自动重传，容器启动后重传成功
- **批量操作**：支持批量添加容器、批量删除容器，添加时自动检测端口
- **搜索过滤**：容器列表支持实时搜索，快速定位目标容器
- **多端口支持**：支持 TCP/UDP 多端口容器（如媒体服务器）
- **Web UI**：简洁易用的管理界面，支持中/英/繁体三语切换
- **亮色/暗色主题**：高对比度，适合各种使用环境
- **安全认证**：管理员密码 SHA-256 哈希加密存储，防止未授权访问
- **自动 CI/CD**：GitHub Actions 自动构建多架构镜像（amd64/arm64）

## 🚀 优势

| 特性 | 说明 |
|------|------|
| **资源节约** | 闲置容器释放 CPU 和内存，降低主机负载 |
| **双模式灵活选择** | Pause 模式毫秒级唤醒；Stop 模式彻底释放内存 |
| **无需修改容器** | 监听宿主机网络接口，容器本身无需修改 |
| **低开销** | 使用 AF_PACKET 原始套接字，性能优于 iptables |
| **即插即用** | 一行 docker-compose.yml 即可部署 |
| **持久化配置** | 配置保存在 `config.json`，容器重启不丢失 |
| **密码加密** | SHA-256 哈希存储，不存明文 |

## 🔄 两种休眠模式对比

| | Pause 模式 | Stop 模式 |
|---|---|---|
| **CPU** | ✅ 释放 | ✅ 释放 |
| **内存** | ❌ 保留 | ✅ 释放 |
| **网络栈** | 保持活跃 | 销毁 |
| **唤醒方式** | `docker unpause` | `docker start` |
| **唤醒速度** | 毫秒级 | 秒级（需启动进程） |
| **适用场景** | 需要快速响应的服务 | 内存占用大且不常用的服务 |

### Stop 模式工作原理

Stop 后容器端口关闭，客户端会收到 `connection refused`。解决方案：
1. **stop 前**：为每个 TCP 端口添加 `iptables -I INPUT -p tcp --dport <port> -j DROP`
2. **DROP 效果**：SYN 包被静默丢弃，客户端 TCP 自动重传（而非收到 RST 报错）
3. **双重检测**：AF_PACKET 嗅探 + iptables DROP 规则命中计数，确保可靠唤醒
4. **检测到流量** → `docker start` → 移除 DROP 规则 → 客户端重传成功

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
2. **iptables 规则**：Stop 模式需要直接操作宿主机 iptables
3. **容器直通**：容器可以直接使用宿主机网络接口

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

## 🔐 默认密码与安全

**初始管理员密码**: `admin123`

密码使用 SHA-256 哈希加密存储，配置文件中不保存明文。首次启动时如果检测到旧版明文密码，会自动转换为哈希。

首次登录后请立即修改密码：
1. 点击右上角「设置」按钮
2. 在「修改管理密码」区域输入当前密码和新密码
3. 点击「修改」保存

### 安全建议

1. **修改默认密码**：首次登录后立即修改密码
2. **限制访问**：使用防火墙限制只有内网可访问 Web UI
3. **不暴露端口**：`LISTEN_HOST` 默认 `0.0.0.0`，如需限制可改为 `127.0.0.1`

## 📝 工作流程

1. 启动后监听指定网络接口的所有流量
2. 轮询所有运行中的容器，检测端口流量
3. 当容器空闲超过设定时间，根据模式自动 pause 或 stop
4. 检测到访问请求时，自动唤醒（pause→unpause，stop→start）
5. 所有配置保存到 `config.json`，重启后自动加载

### 配置休眠模式

在 Web UI 中添加或编辑容器时，可以选择睡眠模式：
- **暂停（保留内存）**：Pause 模式，释放 CPU，毫秒级唤醒
- **停止（释放全部）**：Stop 模式，释放 CPU + 内存，秒级唤醒

每个容器可以独立配置不同的休眠模式。

### 添加和编辑容器

- **添加容器**：支持多选批量添加，自动检测每个容器的端口，容器名称旁显示端口协议和端口号（如 `tcp:1101、1303 udp:2204`）
- **编辑容器**：修改已休眠容器的睡眠模式时会自动先唤醒容器，确保状态一致
- **搜索过滤**：容器列表上方有搜索框，实时过滤
- **批量删除**：通过复选框选中多个容器后一键删除

## 📜 更新日志

### v1.4.0（2026-08-21）
- **修复**：已休眠容器修改睡眠模式后无法唤醒的问题（编辑前自动唤醒）
- **修复**：添加已休眠容器时状态不一致的问题（添加前自动唤醒）
- **修复**：Stop 模式容器唤醒失败（增加 iptables DROP 规则命中计数检测）
- **修复**：事件队列竞态条件导致唤醒丢失

### v1.3.0（2026-08-21）
- **新增**：批量添加容器（多选 + 自动检测端口）
- **新增**：批量删除容器（复选框 + 一键删除）
- **新增**：容器列表搜索框（实时过滤）
- **新增**：添加容器对话框中显示端口协议和端口号
- **新增**：密码 SHA-256 哈希加密存储
- **修复**：`deleteText` 未定义导致容器列表不显示

### v1.2.0（2026-08-21）
- **新增**：Stop 模式（释放全部资源包括内存）
- **新增**：iptables DROP 规则拦截 SYN，实现 Stop 模式无缝唤醒
- **新增**：睡眠模式选择器（Pause/Stop）
- **新增**：容器列表显示模式列
- **新增**：停止/唤醒按钮根据模式自动切换

### v1.1.0（2026-08-20）
- **新增**：GitHub Actions 自动构建多架构镜像（amd64/arm64）
- **新增**：Dockerfile 和 requirements.txt
- **新增**：繁体中文版 README
- **新增**：默认密码说明
- **新增**：Star 请求

### v1.0.0（2026-08-15）
- **初始版本**
- AF_PACKET 包嗅探检测容器流量
- Pause/Unpause 自动休眠和唤醒
- 事件驱动唤醒（检测到流量立即唤醒）
- Web UI 管理界面
- 管理员密码登录认证
- 多端口容器支持
- 多语言支持（简体中文/繁体中文/英文）
- 亮色/暗色主题
- 容器编辑功能（端口、空闲超时）
- 全局设置（主题、语言、空闲超时、检查间隔）

---

**如果觉得好用，欢迎点个 Star，感谢支持！⭐**

---

<a name="traditional"></a>
## 🇹🇼 繁體中文版

# Docker Pause Manager

基於 AF_PACKET 包嗅探的 Docker 容器自動休眠管理器。當容器在設定時間內沒有網路流量時自動休眠釋放資源，有訪問時自動喚醒。支援 **Pause 模式**（釋放 CPU，保留記憶體）和 **Stop 模式**（釋放全部資源包括記憶體）。

## ✨ 專案特點

- **雙模式休眠**：支援 Pause（釋放 CPU，保留記憶體）和 Stop（釋放全部資源）兩種模式，按容器單獨配置
- **智慧休眠**：基於真實網路流量檢測（AF_PACKET + iptables 雙重檢測），準確率高
- **自動喚醒**：偵測到訪問請求後瞬間喚醒，用戶無感知
- **Stop 模式無縫喚醒**：透過 iptables DROP 規則攔截 SYN 封包，使客戶端 TCP 自動重傳，容器啟動後重傳成功
- **批次操作**：支援批次新增容器、批次刪除容器，新增時自動偵測埠
- **搜尋過濾**：容器列表支援即時搜尋，快速定位目標容器
- **多埠支援**：支援 TCP/UDP 多埠容器（如媒體伺服器）
- **Web UI**：簡潔易用的管理介面，支援中/英/繁體三語切換
- **亮色/暗色主題**：高對比度，適合各種使用環境
- **安全認證**：管理員密碼 SHA-256 雜湊加密儲存，防止未授權訪問
- **自動 CI/CD**：GitHub Actions 自動建構多架構映像（amd64/arm64）

## 🚀 優勢

| 特性 | 說明 |
|------|------|
| **資源節約** | 閒置容器釋放 CPU 和記憶體，降低主機負載 |
| **雙模式靈活選擇** | Pause 模式毫秒級喚醒；Stop 模式徹底釋放記憶體 |
| **無需修改容器** | 監聽宿主機網路介面，容器本身無需修改 |
| **低開銷** | 使用 AF_PACKET 原始套接字，效能優於 iptables |
| **即插即用** | 一行 docker-compose.yml 即可部署 |
| **持久化配置** | 配置保存在 `config.json`，容器重啟不丟失 |
| **密碼加密** | SHA-256 雜湊儲存，不存明文 |

## 🔄 兩種休眠模式對比

| | Pause 模式 | Stop 模式 |
|---|---|---|
| **CPU** | ✅ 釋放 | ✅ 釋放 |
| **記憶體** | ❌ 保留 | ✅ 釋放 |
| **網路棧** | 保持活躍 | 銷毀 |
| **喚醒方式** | `docker unpause` | `docker start` |
| **喚醒速度** | 毫秒級 | 秒級（需啟動程序） |
| **適用場景** | 需要快速響應的服務 | 記憶體佔用大且不常用的服務 |

### Stop 模式工作原理

Stop 後容器埠關閉，客戶端會收到 `connection refused`。解決方案：
1. **stop 前**：為每個 TCP 埠新增 `iptables -I INPUT -p tcp --dport <port> -j DROP`
2. **DROP 效果**：SYN 封包被靜默丟棄，客戶端 TCP 自動重傳（而非收到 RST 報錯）
3. **雙重檢測**：AF_PACKET 嗅探 + iptables DROP 規則命中計數，確保可靠喚醒
4. **偵測到流量** → `docker start` → 移除 DROP 規則 → 客戶端重傳成功

## 📋 docker-compose.yml 詳解

```yaml
services:
  docker-pause-manager:
    image: ghcr.io/jiushizhu2024/docker-pause-manager:latest  # 映像地址
    container_name: docker-pause-manager                       # 容器名稱
    restart: unless-stopped                                    # 自動重啟策略
    network_mode: host                                         # 主機網路（必需：用於 AF_PACKET 嗅探）
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock             # Docker Socket（必需）
    environment:
      - CONFIG_PATH=/app/config.json                          # 配置檔案路徑
      - STATE_PATH=/app/state.json                            # 狀態檔案路徑
      - LISTEN_HOST=0.0.0.0                                   # 監聽地址（0.0.0.0=所有介面）
      - LISTEN_PORT=5287                                      # Web UI 埠
    cap_add:
      - NET_ADMIN                                             # 網路管理權限（必需）
      - NET_RAW                                               # 原始套接字權限（必需）
```

### 網路模式說明

`network_mode: host` 是必需的，原因：
1. **AF_PACKET 包嗅探**：需要監聽所有網路流量，host 網路才能捕獲宿主機流量
2. **iptables 規則**：Stop 模式需要直接操作宿主機 iptables
3. **容器直通**：容器可以直接使用宿主機網路介面

> **注意**：host 模式下不能使用 `ports` 映射，埠透過 `LISTEN_PORT` 環境變數配置。

## 🛠️ 部署步驟

### 方法一：直接複製 docker-compose.yml（推薦）

```bash
# 1. 建立目錄
mkdir docker-pause-manager && cd docker-pause-manager

# 2. 建立 docker-compose.yml 檔案
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

# 3. 啟動容器
docker compose up -d

# 4. 訪問 Web UI
# http://<伺服器IP>:5287
# 預設密碼: admin123
```

### 方法二：克隆倉庫

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

## ⚙️ 配置說明

### 環境變數

| 變數 | 說明 | 預設值 |
|------|------|--------|
| `LISTEN_HOST` | 監聽地址 | `0.0.0.0` |
| `LISTEN_PORT` | Web UI 埠 | `5287` |
| `CONFIG_PATH` | 配置檔案路徑 | `/app/config.json` |
| `STATE_PATH` | 狀態檔案路徑 | `/app/state.json` |

### 修改埠

```yaml
environment:
  - LISTEN_PORT=8080  # 改為 8080
```

## 🔐 預設密碼與安全

**初始管理員密碼**: `admin123`

密碼使用 SHA-256 雜湊加密儲存，配置檔案中不保存明文。首次啟動時如果偵測到舊版明文密碼，會自動轉換為雜湊。

首次登入後請立即修改密碼：
1. 點擊右上角「設定」按鈕
2. 在「修改管理密碼」區域輸入當前密碼和新密碼
3. 點擊「修改」儲存

### 安全建議

1. **修改預設密碼**：首次登入後立即修改密碼
2. **限制訪問**：使用防火牆限制只有內網可訪問 Web UI
3. **不暴露埠**：`LISTEN_HOST` 預設 `0.0.0.0`，如需限制可改為 `127.0.0.1`

## 📝 工作流程

1. 啟動後監聽指定網路介面的所有流量
2. 輪詢所有執行中的容器，檢測埠流量
3. 當容器空閒超過設定時間，根據模式自動 pause 或 stop
4. 偵測到訪問請求時，自動喚醒（pause→unpause，stop→start）
5. 所有配置儲存到 `config.json`，重啟後自動載入

### 配置休眠模式

在 Web UI 中新增或編輯容器時，可以選擇睡眠模式：
- **暫停（保留記憶體）**：Pause 模式，釋放 CPU，毫秒級喚醒
- **停止（釋放全部）**：Stop 模式，釋放 CPU + 記憶體，秒級喚醒

每個容器可以獨立配置不同的休眠模式。

### 新增和編輯容器

- **新增容器**：支援多選批次新增，自動偵測每個容器的埠，容器名稱旁顯示埠協定和埠號（如 `tcp:1101、1303 udp:2204`）
- **編輯容器**：修改已休眠容器的睡眠模式時會自動先喚醒容器，確保狀態一致
- **搜尋過濾**：容器列表上方有搜尋框，即時過濾
- **批次刪除**：透過核取方塊選取多個容器後一鍵刪除

## 📜 更新日誌

### v1.4.0（2026-08-21）
- **修復**：已休眠容器修改睡眠模式後無法喚醒的問題（編輯前自動喚醒）
- **修復**：新增已休眠容器時狀態不一致的問題（新增前自動喚醒）
- **修復**：Stop 模式容器喚醒失敗（增加 iptables DROP 規則命中計數偵測）
- **修復**：事件佇列競態條件導致喚醒丟失

### v1.3.0（2026-08-21）
- **新增**：批次新增容器（多選 + 自動偵測埠）
- **新增**：批次刪除容器（核取方塊 + 一鍵刪除）
- **新增**：容器列表搜尋框（即時過濾）
- **新增**：新增容器對話框中顯示埠協定和埠號
- **新增**：密碼 SHA-256 雜湊加密儲存
- **修復**：`deleteText` 未定義導致容器列表不顯示

### v1.2.0（2026-08-21）
- **新增**：Stop 模式（釋放全部資源包括記憶體）
- **新增**：iptables DROP 規則攔截 SYN，實現 Stop 模式無縫喚醒
- **新增**：睡眠模式選擇器（Pause/Stop）
- **新增**：容器列表顯示模式列
- **新增**：停止/喚醒按鈕根據模式自動切換

### v1.1.0（2026-08-20）
- **新增**：GitHub Actions 自動建構多架構映像（amd64/arm64）
- **新增**：Dockerfile 和 requirements.txt
- **新增**：繁體中文版 README
- **新增**：預設密碼說明
- **新增**：Star 請求

### v1.0.0（2026-08-15）
- **初始版本**
- AF_PACKET 封包嗅探偵測容器流量
- Pause/Unpause 自動休眠和喚醒
- 事件驅動喚醒（偵測到流量立即喚醒）
- Web UI 管理介面
- 管理員密碼登入認證
- 多埠容器支援
- 多語言支援（簡體中文/繁體中文/英文）
- 亮色/暗色主題
- 容器編輯功能（埠、閒置超時）
- 全域設定（主題、語言、閒置超時、檢查間隔）

---

**如果覺得好用，歡迎點個 Star，感謝支持！⭐**

---

<a name="english"></a>
## 🇬🇧 English Version

# Docker Pause Manager

Docker container auto-sleep manager based on AF_PACKET packet sniffing. Automatically sleeps containers with no network traffic for a set time, and wakes them on demand. Supports **Pause mode** (release CPU, keep memory) and **Stop mode** (release all resources including memory).

## ✨ Features

- **Dual Sleep Modes**: Supports Pause (release CPU, keep memory) and Stop (release all resources) modes, configurable per container
- **Smart Sleep Detection**: Based on real network traffic analysis (AF_PACKET + iptables dual detection), high accuracy
- **Instant Wake-up**: Automatically wakes when access is detected, transparent to users
- **Stop Mode Seamless Wake-up**: Uses iptables DROP rules to intercept SYN packets, causing client TCP to auto-retransmit, succeeding after container starts
- **Batch Operations**: Batch add containers, batch delete containers, auto-detect ports on add
- **Search Filter**: Real-time search in container lists, quick filtering
- **Multi-port Support**: Supports containers with multiple TCP/UDP ports
- **Web UI**: Clean and intuitive management interface, supports CN/EN/Traditional Chinese
- **Light/Dark Theme**: High contrast, suitable for various environments
- **Secure Authentication**: Admin password stored as SHA-256 hash, prevents unauthorized access
- **Auto CI/CD**: GitHub Actions auto-builds multi-arch images (amd64/arm64)

## 🚀 Advantages

| Feature | Description |
|---------|-------------|
| **Resource Saving** | Idle containers release CPU and memory, reducing host load |
| **Flexible Mode Selection** | Pause mode for millisecond wake-up; Stop mode for full memory release |
| **No Container Modification** | Listens on host network interfaces, containers unchanged |
| **Low Overhead** | Uses AF_PACKET raw sockets, better performance than iptables |
| **Plug & Play** | One-line docker-compose.yml to deploy |
| **Persistent Config** | Config saved to `config.json`, survives container restart |
| **Password Encryption** | SHA-256 hash storage, no plaintext |

## 🔄 Sleep Mode Comparison

| | Pause Mode | Stop Mode |
|---|---|---|
| **CPU** | ✅ Released | ✅ Released |
| **Memory** | ❌ Kept | ✅ Released |
| **Network Stack** | Active | Destroyed |
| **Wake Method** | `docker unpause` | `docker start` |
| **Wake Speed** | Milliseconds | Seconds (process startup) |
| **Use Case** | Services needing fast response | High-memory, rarely used services |

### Stop Mode Technical Details

After stop, container ports close and clients get `connection refused`. Solution:
1. **Before stop**: Add `iptables -I INPUT -p tcp --dport <port> -j DROP` for each TCP port
2. **DROP effect**: SYN packets silently dropped, client TCP auto-retransmits (instead of getting RST error)
3. **Dual detection**: AF_PACKET sniffing + iptables DROP rule hit count, ensuring reliable wake-up
4. **Traffic detected** → `docker start` → remove DROP rules → client retransmission succeeds

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
2. **iptables Rules**: Stop mode needs to directly manipulate host iptables
3. **Container Pass-through**: Container can directly use host network interfaces

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

## 🔐 Default Password & Security

**Initial Admin Password**: `admin123`

Password is stored as SHA-256 hash, no plaintext in config file. On first startup with old plaintext password, it auto-migrates to hash.

Change password after first login:
1. Click the Settings button (top right)
2. In the "Change Password" section, enter current and new password
3. Click "Change" to save

### Security Tips

1. **Change Default Password**: Change password immediately after first login
2. **Limit Access**: Use firewall to restrict Web UI to internal network only
3. **Bind to Local**: Set `LISTEN_HOST` to `127.0.0.1` to restrict to localhost only

## 📝 How It Works

1. On startup, monitor all traffic on specified network interfaces
2. Poll all running containers, detect port traffic
3. Auto-sleep containers idle beyond the set time (pause or stop based on mode)
4. Auto-wake when access requests are detected (pause→unpause, stop→start)
5. All configs saved to `config.json`, auto-loaded on restart

### Configuring Sleep Mode

When adding or editing a container in the Web UI, you can select the sleep mode:
- **Pause (keep memory)**: Pause mode, releases CPU, millisecond wake-up
- **Stop (release all)**: Stop mode, releases CPU + memory, second-level wake-up

Each container can be independently configured with a different sleep mode.

### Adding and Editing Containers

- **Add Container**: Supports multi-select batch add, auto-detects ports per container, shows port protocol and number next to name (e.g. `tcp:1101,1303 udp:2204`)
- **Edit Container**: Automatically wakes sleeping containers before changing sleep mode, ensuring consistent state
- **Search Filter**: Search box above container list for real-time filtering
- **Batch Delete**: Select multiple containers via checkboxes, one-click delete

## 📜 Changelog

### v1.4.0 (2026-08-21)
- **Fix**: Sleeping containers couldn't wake up after changing sleep mode (auto-wake before edit)
- **Fix**: Inconsistent state when adding already-sleeping containers (auto-wake before add)
- **Fix**: Stop mode containers failed to wake up (added iptables DROP rule hit count detection)
- **Fix**: Event queue race condition causing lost wake-ups

### v1.3.0 (2026-08-21)
- **Add**: Batch add containers (multi-select + auto port detection)
- **Add**: Batch delete containers (checkboxes + one-click delete)
- **Add**: Container list search box (real-time filtering)
- **Add**: Port protocol and number display in add container dialog
- **Add**: Password SHA-256 hash encryption storage
- **Fix**: `deleteText` undefined causing empty container list

### v1.2.0 (2026-08-21)
- **Add**: Stop mode (release all resources including memory)
- **Add**: iptables DROP rules to intercept SYN for seamless Stop mode wake-up
- **Add**: Sleep mode selector (Pause/Stop)
- **Add**: Mode column in container list
- **Add**: Stop/Wake buttons auto-switch based on mode

### v1.1.0 (2026-08-20)
- **Add**: GitHub Actions auto-build multi-arch images (amd64/arm64)
- **Add**: Dockerfile and requirements.txt
- **Add**: Traditional Chinese README
- **Add**: Default password documentation
- **Add**: Star request

### v1.0.0 (2026-08-15)
- **Initial release**
- AF_PACKET packet sniffing for container traffic detection
- Pause/Unpause automatic sleep and wake-up
- Event-driven wake-up (instant wake on traffic detection)
- Web UI management interface
- Admin password login authentication
- Multi-port container support
- Multi-language support (Simplified Chinese/Traditional Chinese/English)
- Light/dark theme
- Container editing (ports, idle timeout)
- Global settings (theme, language, idle timeout, check interval)

---

**If you find this useful, please consider giving it a Star, thank you! ⭐**

---

## 🤝 Contributing

Pull requests welcome!

## 📄 License

MIT License