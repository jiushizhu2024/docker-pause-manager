#!/usr/bin/env python3
"""
Docker Pause Manager - Web UI
自动 pause/unpause Docker 容器，基于 AF_PACKET 包嗅探检测流量，空闲超时 pause。
支持多端口容器：一个容器可监控多个 TCP/UDP 端口，有流量即唤醒，全部空闲才 pause。
支持管理员登录认证、自动端口选择。
"""
import json, os, time, threading, subprocess, logging, hashlib, secrets, socket as socketlib, struct, re
from functools import wraps
from flask import Flask, request, jsonify, send_file, send_from_directory, make_response

# ===== Docker SDK 兼容性修复 =====
# 使用 requests-unixsocket2 替代 docker SDK 自带适配器
import requests_unixsocket as _rus
import requests as _req

# 创建 Unix socket 会话
_unix_session = _rus.Session()
DOCKER_SOCKET = "unix://%2Fvar%2Frun%2Fdocker.sock"

def _docker_api(method, path, **kwargs):
    """直接调用 Docker API（通过 Unix socket）"""
    url = f"http+unix://%2Fvar%2Frun%2Fdocker.sock{path}"
    r = _unix_session.request(method, url, **kwargs)
    if r.status_code == 204:
        return None
    if r.status_code >= 400:
        raise Exception(f"Docker API error {r.status_code}: {r.text}")
    return r.json()

def _docker_api_stream(method, path, **kwargs):
    """流式调用 Docker API"""
    url = f"http+unix://%2Fvar%2Frun%2Fdocker.sock{path}"
    return _unix_session.request(method, url, stream=True, **kwargs)

# ===== Docker 容器操作封装 =====

class DockerContainer:
    def __init__(self, container_id, name, status):
        self.id = container_id
        self.short_id = container_id[:12]
        self.name = name
        self.status = status
        self._attrs = {}

    def pause(self):
        _docker_api("POST", f"/containers/{self.id}/pause")

    def unpause(self):
        _docker_api("POST", f"/containers/{self.id}/unpause")

    def start(self):
        _docker_api("POST", f"/containers/{self.id}/start")

def get_container(container_name):
    """获取容器信息"""
    try:
        data = _docker_api("GET", f"/containers/{container_name}/json")
        return DockerContainer(
            data["Id"],
            data["Name"].lstrip("/"),
            data["State"]["Status"]
        )
    except Exception:
        return None

def pause_container(container_name):
    """暂停容器"""
    container = get_container(container_name)
    if container and container.status == "running":
        try:
            container.pause()
            log.info(f"[{container_name}] 已暂停 (pause)")
            return True
        except Exception as e:
            log.error(f"[{container_name}] 暂停失败: {e}")
            return False
    return False

def unpause_container(container_name):
    """恢复容器"""
    container = get_container(container_name)
    if container and container.status == "paused":
        try:
            container.unpause()
            log.info(f"[{container_name}] 已唤醒 (unpause)")
            return True
        except Exception as e:
            log.error(f"[{container_name}] 唤醒失败: {e}")
            return False
    return False

def start_container(container_name):
    """启动已退出的容器（重启后恢复）"""
    container = get_container(container_name)
    if container and container.status == "exited":
        try:
            container.start()
            log.info(f"[{container_name}] 已启动 (start)")
            return True
        except Exception as e:
            log.error(f"[{container_name}] 启动失败: {e}")
            return False
    return False

def get_container_state(container_name):
    """获取容器状态信息"""
    container = get_container(container_name)
    if not container:
        return {"status": "not_found", "name": container_name}
    return {
        "name": container.name,
        "status": container.status,
        "id": container.short_id,
    }

try:
    import jwt
except ImportError:
    jwt = None

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/app/config.json")
STATE_PATH = os.environ.get("STATE_PATH", "/app/state.json")

# 默认配置
DEFAULT_CONFIG = {
    "admin_password": "admin123",
    "idle_timeout": 300,
    "check_interval": 10,
    "net_interface": "eth0",
    "containers": {}
}

LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "5287"))
JWT_SECRET = secrets.token_hex(32)
JWT_ALGORITHM = "HS256"
JWT_EXP_HOURS = 24

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pause-manager")

# ===== 启动时确保配置文件存在 =====
def ensure_config_file(path, default_content):
    """确保配置文件存在，不存在则创建默认内容"""
    if not os.path.exists(path):
        log.info(f"[startup] {path} 不存在，正在创建默认配置...")
        dir_name = os.path.dirname(path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        with open(path, "w") as f:
            json.dump(default_content, f, ensure_ascii=False, indent=2)
        log.info(f"[startup] {path} 默认配置已创建")

ensure_config_file(CONFIG_PATH, DEFAULT_CONFIG)
ensure_config_file(STATE_PATH, {})

app = Flask(__name__, static_folder=None)

# 读取配置
def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

CONFIG = load_config()

# 管理员密码
ADMIN_PASSWORD = CONFIG.get("admin_password", "admin123")

# 连续空闲超时（秒），默认 5 分钟
DEFAULT_IDLE_TIMEOUT = CONFIG.get("idle_timeout", 300)

# 轮询间隔（秒）
CHECK_INTERVAL = CONFIG.get("check_interval", 10)

# 监控的容器列表
MONITORED_CONTAINERS = CONFIG.get("containers", {})
NETWORK_INTERFACE = CONFIG.get("net_interface", "eth0")

# ========== AF_PACKET 包嗅探 ==========
class PacketCounter:
    """使用 AF_PACKET 嗅探网络包，统计目标端口流量"""
    
    ETHERTYPE_IP = 0x0800
    PROTO_TCP = 6
    PROTO_UDP = 17
    
    def __init__(self, interface, ports):
        self.interface = interface
        self.ports = set(ports)
        self.packet_counts = {}  # {port: count}
        self.lock = threading.Lock()
        self.running = False
        self.thread = None
        self.sock = None
        self.interfaces = []
    
    def start(self, ports=None):
        """启动包嗅探线程（监听所有相关接口）"""
        if self.running:
            return
        if ports is not None:
            self.ports = set(ports)
        # 自动发现 Docker bridge 接口和物理接口
        self.interfaces = self._discover_interfaces()
        log.info(f"[AF_PACKET] 接口列表: {self.interfaces}")
        
        self.running = True
        self.sock = None
        self.thread = threading.Thread(target=self._rx_loop, daemon=True)
        self.thread.start()
        time.sleep(1)  # 等待 socket 初始化
        if self.sock:
            log.info(f"[AF_PACKET] 启动包嗅探，监听 {self.interfaces}，端口 {sorted(self.ports)}")
        else:
            log.error("[AF_PACKET] 启动失败")
    
    def _discover_interfaces(self):
        """发现需要监听的网络接口（物理接口 + Docker bridge）"""
        interfaces = []
        try:
            # 添加物理接口
            interfaces.append(self.interface)
            # 添加 Docker bridge 接口（从 /proc/net/dev 或 ip 命令）
            try:
                import subprocess
                result = subprocess.run(["ip", "link", "show"], capture_output=True, text=True, timeout=5)
                for line in result.stdout.split("\n"):
                    if line.strip().startswith("br-") and ": <" in line:
                        iface = line.split(":")[0].strip()
                        interfaces.append(iface)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                # 如果 ip 命令不可用，尝试从 /proc/net/dev 读取
                try:
                    with open("/proc/net/dev", "r") as f:
                        for line in f:
                            if line.strip().startswith("br-"):
                                iface = line.split(":")[0].strip()
                                interfaces.append(iface)
                except:
                    pass
        except Exception as e:
            log.debug(f"发现接口失败: {e}")
        return list(set(interfaces))  # 去重
    
    def stop(self):
        """停止包嗅探"""
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
        if self.thread:
            self.thread.join(timeout=2)
        log.info("[AF_PACKET] 包嗅探已停止")
    
    def _rx_loop(self):
        """接收并解析网络包（监听多个接口）"""
        import threading

        sockets = []

        def _listen_iface(iface):
            sock = None
            try:
                sock = socketlib.socket(socketlib.AF_PACKET, socketlib.SOCK_RAW, socketlib.htons(self.ETHERTYPE_IP))
                sock.bind((iface, self.ETHERTYPE_IP))
                sock.settimeout(1.0)
                log.info(f"[AF_PACKET] 监听接口 {iface}")
                while self.running:
                    try:
                        data = sock.recv(2048)
                        self._parse_packet(data)
                    except socketlib.timeout:
                        continue
                    except Exception as e:
                        if self.running:
                            log.debug(f"[AF_PACKET] {iface}: {e}")
            except Exception as e:
                log.warning(f"[AF_PACKET] 启动 {iface} 失败: {e}")
            finally:
                if sock:
                    try:
                        sock.close()
                    except:
                        pass

        # 启动多线程监听所有接口
        threads = []
        for iface in self.interfaces:
            t = threading.Thread(target=_listen_iface, args=(iface,), daemon=True)
            t.start()
            threads.append(t)

        # 等待所有线程
        while self.running:
            time.sleep(1)

    def _parse_packet(self, data):
        """解析以太网帧"""
        if len(data) < 34:  # 最小以太网帧 + IP 头
            return
        
        # 跳过以太网头（14字节）
        eth_type = struct.unpack('!H', data[12:14])[0]
        if eth_type != self.ETHERTYPE_IP:
            return
        
        # IP 头
        ip_header = data[14:]
        if len(ip_header) < 20:
            return
        
        proto = ip_header[9]
        total_len = struct.unpack('!H', ip_header[2:4])[0]
        
        if proto == self.PROTO_TCP:
            # TCP 头（20字节）
            if len(ip_header) < 32:
                return
            src_port = struct.unpack('!H', ip_header[20:22])[0]
            dst_port = struct.unpack('!H', ip_header[22:24])[0]
            self._count_port(src_port)
            self._count_port(dst_port)
        
        elif proto == self.PROTO_UDP:
            # UDP 头（8字节）
            if len(ip_header) < 28:
                return
            src_port = struct.unpack('!H', ip_header[20:22])[0]
            dst_port = struct.unpack('!H', ip_header[22:24])[0]
            self._count_port(src_port)
            self._count_port(dst_port)
    
    def _count_port(self, port):
        """统计端口流量"""
        if port in self.ports:
            with self.lock:
                self.packet_counts[port] = self.packet_counts.get(port, 0) + 1
    
    def get_count(self, port):
        """获取指定端口的包计数"""
        with self.lock:
            return self.packet_counts.get(port, 0)
    
    def reset_count(self, port):
        """重置包计数"""
        with self.lock:
            self.packet_counts[port] = 0


# 全局包计数器
packet_counter = None

def init_packet_counter():
    """根据配置初始化包计数器"""
    global packet_counter
    if packet_counter:
        packet_counter.stop()
    
    all_ports = set()
    for cfg in MONITORED_CONTAINERS.values():
        for port_info in cfg.get("ports", []):
            all_ports.add(int(port_info.get("port", 0)))
    
    if not all_ports:
        return
    
    packet_counter = PacketCounter(NETWORK_INTERFACE, all_ports)
    packet_counter.start()


# ========== 登录认证 ==========

def verify_token(token):
    """验证 token - 使用 SHA256 token"""
    return token == hashlib.sha256(f"dpm-{ADMIN_PASSWORD}".encode()).hexdigest()

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
        else:
            token = request.args.get("token", "")
        if not verify_token(token):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# ========== conntrack 监控 ==========

def get_connection_count(port, proto="tcp"):
    """通过 conntrack 统计指定端口的连接数"""
    try:
        result = subprocess.run(
            ["conntrack", "-L", "-p", proto, "-d", f":{port}"],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.strip().split("\n")
        established = 0
        for line in lines:
            if "ESTABLISHED" in line or "ASSURED" in line:
                established += 1
        return established
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 0

def has_new_connection(container_name, ports):
    """检查是否有流量（使用 AF_PACKET 包嗅探）"""
    global packet_counter
    if packet_counter is None:
        # 回退到 conntrack
        return _has_new_connection_conntrack(container_name, ports)
    
    try:
        for port_info in ports:
            port = int(port_info.get("port", 0))
            if port in packet_counter.ports:
                count = packet_counter.get_count(port)
                if count > 0:
                    log.info(f"[{container_name}] 检测到流量 (port={port}, packets={count})")
                    packet_counter.reset_count(port)
                    return True
        return False
    except Exception as e:
        log.debug(f"[{container_name}] 检查流量失败: {e}")
        return False

def _has_new_connection_conntrack(container_name, ports):
    """备用：通过 conntrack 检测新连接"""
    try:
        for port_info in ports:
            port = port_info.get("port", 80)
            proto = port_info.get("proto", "tcp")
            result = subprocess.run(
                ["conntrack", "-L", "-p", proto, "--state", "NEW", "-d", f":{port}"],
                capture_output=True, text=True, timeout=5
            )
            lines = result.stdout.strip().split("\n")
            if lines and lines[0]:
                if any("NEW" in line for line in lines):
                    log.info(f"[{container_name}] 检测到新连接 (port={port}/{proto})")
                    return True
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False

# ========== 容器监控循环 ==========

class ContainerMonitor:
    def __init__(self):
        self.container_states = {}
        self.lock = threading.Lock()
        self.running = True
        self._load_state()

    def _save_state(self):
        """持久化状态，重启后恢复 paused-by-us 标记"""
        try:
            state = {}
            for name, s in self.container_states.items():
                state[name] = {
                    "is_paused_by_us": s.get("is_paused_by_us", False),
                    "restored_from_reboot": False,
                }
            state_dir = os.path.dirname(STATE_PATH)
            if state_dir:
                os.makedirs(state_dir, exist_ok=True)
            with open(STATE_PATH, "w") as f:
                json.dump(state, f)
        except Exception as e:
            log.warning(f"保存状态失败: {e}")

    def _load_state(self):
        """启动时恢复状态"""
        try:
            if os.path.exists(STATE_PATH):
                with open(STATE_PATH, "r") as f:
                    return json.load(f)
        except Exception as e:
            log.warning(f"加载状态失败: {e}")
        return {}

    def update_config(self, containers_cfg):
        with self.lock:
            saved_state = self._load_state()
            for name, cfg in containers_cfg.items():
                prev = saved_state.get(name, {})
                was_paused_by_us = prev.get("is_paused_by_us", False)
                if name not in self.container_states:
                    self.container_states[name] = {
                        "idle_seconds": 0,
                        "is_paused_by_us": was_paused_by_us,
                        "ports": cfg.get("ports", []),
                        "idle_timeout": cfg.get("idle_timeout", DEFAULT_IDLE_TIMEOUT),
                    }
            for name in list(self.container_states.keys()):
                if name not in containers_cfg:
                    del self.container_states[name]

    def mark_activity(self, container_name):
        with self.lock:
            if container_name in self.container_states:
                self.container_states[container_name]["idle_seconds"] = 0

    def run_check(self):
        with self.lock:
            needs_save = False
            for name, state in self.container_states.items():
                ports = state["ports"]
                idle_timeout = state["idle_timeout"]

                container = get_container(name)
                if not container:
                    continue

                # 重启后恢复：之前被 pause 的容器重启后变成 exited
                if container.status == "exited" and state["is_paused_by_us"]:
                    log.info(f"[{name}] 重启后恢复中 (exited→starting)")
                    start_container(name)
                    state["is_paused_by_us"] = False
                    state["idle_seconds"] = 0
                    needs_save = True
                    continue

                if container.status == "paused" and not state["is_paused_by_us"]:
                    state["is_paused_by_us"] = True
                    state["idle_seconds"] = 0
                    needs_save = True
                    continue

                if container.status == "paused" and state["is_paused_by_us"]:
                    if has_new_connection(name, ports):
                        unpause_container(name)
                        state["idle_seconds"] = 0
                        state["is_paused_by_us"] = False
                        needs_save = True
                    continue

                if container.status == "running":
                    has_active = False
                    for port_info in ports:
                        port = port_info.get("port", 80)
                        proto = port_info.get("proto", "tcp")
                        count = get_connection_count(port, proto)
                        if count > 0:
                            has_active = True
                            break

                    if has_active:
                        state["idle_seconds"] = 0
                    else:
                        state["idle_seconds"] += CHECK_INTERVAL
                        log.debug(f"[{name}] 空闲中 {state['idle_seconds']}s / {idle_timeout}s")
                        if state["idle_seconds"] >= idle_timeout:
                            pause_container(name)
                            state["is_paused_by_us"] = True
                            needs_save = True
            if needs_save:
                self._save_state()

monitor = ContainerMonitor()

def monitor_loop():
    while monitor.running:
        try:
            monitor.run_check()
        except Exception as e:
            log.error(f"监控循环异常: {e}")
        time.sleep(CHECK_INTERVAL)

# ========== Web API ==========

@app.route("/")
def index():
    try:
        return send_from_directory(os.path.dirname(__file__), "templates/index.html")
    except FileNotFoundError:
        resp = make_response("""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
        <meta http-equiv="Pragma" content="no-cache">
        <meta http-equiv="Expires" content="0">
        <title>Docker Pause Manager</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #f1f5f9; color: #1e293b; padding: 20px;
            }
            .container { max-width: 900px; margin: 0 auto; }
            h1 { font-size: 24px; margin-bottom: 20px; color: #0f172a; }
            .card { background: #fff; border-radius: 12px; padding: 20px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
            .card h2 { font-size: 18px; margin-bottom: 12px; }
            .status-badge { display: inline-block; padding: 4px 12px; border-radius: 20px; font-size: 13px; font-weight: 600; }
            .status-running { background: #dcfce7; color: #166534; }
            .status-paused { background: #fef3c7; color: #92400e; }
            .status-exited { background: #fce4ec; color: #c62828; }
            .status-not_found { background: #f1f5f9; color: #64748b; }
            .btn { display: inline-block; padding: 8px 16px; border-radius: 8px; border: none; cursor: pointer; font-size: 14px; font-weight: 500; transition: background 0.2s; }
            .btn-primary { background: #2563eb; color: #fff; }
            .btn-primary:hover { background: #1d4ed8; }
            .btn-success { background: #16a34a; color: #fff; }
            .btn-success:hover { background: #15803d; }
            .btn-warning { background: #d97706; color: #fff; }
            .btn-warning:hover { background: #b45309; }
            .login-form { margin-bottom: 20px; }
            .login-form input { padding: 8px 12px; border: 1px solid #cbd5e1; border-radius: 8px; font-size: 14px; }
            .login-form input:focus { outline: none; border-color: #2563eb; }
            .login-form button { margin-left: 8px; }
            .hidden { display: none; }
            table { width: 100%; border-collapse: collapse; font-size: 14px; }
            th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #e2e8f0; }
            th { color: #64748b; font-weight: 600; }
            .stats { display: flex; gap: 16px; margin-bottom: 16px; }
            .stat-box { flex: 1; background: #fff; padding: 16px; border-radius: 12px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
            .stat-box .num { font-size: 28px; font-weight: 700; }
            .stat-box .label { font-size: 13px; color: #64748b; }
            .toast { position: fixed; top: 20px; right: 20px; padding: 12px 20px; border-radius: 8px; color: #fff; font-size: 14px; z-index: 999; display: none; }
            .toast-success { background: #16a34a; }
            .toast-error { background: #dc2626; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🐳 Docker Pause Manager</h1>
            <div class="login-form" id="loginForm">
                <form onsubmit="event.preventDefault(); login();">
                    <input type="password" id="password" placeholder="管理员密码" />
                    <button class="btn btn-primary" type="submit">登录</button>
                </form>
            </div>
            <div id="mainContent" class="hidden">
                <div class="stats" id="stats"></div>
                <div class="card">
                    <h2>容器状态</h2>
                    <div id="containerList"></div>
                </div>
                <div class="card">
                    <h2>配置</h2>
                    <p style="font-size:14px;color:#64748b;margin-bottom:12px;">
                        在 <code>config.json</code> 中配置监控的容器，或通过 API 动态管理。
                    </p>
                    <button class="btn btn-primary" onclick="refreshStatus()">刷新状态</button>
                </div>
            </div>
        </div>
        <div id="toast" class="toast"></div>
        <script>
            let token = '';
            function showToast(msg, type='success') {
                const t = document.getElementById('toast');
                t.textContent = msg; t.className = 'toast toast-' + type; t.style.display = 'block';
                setTimeout(() => t.style.display = 'none', 3000);
            }
            function login() {
                const pwd = document.getElementById('password').value;
                console.log('登录中...', pwd ? '密码已输入' : '密码为空');
                fetch('/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({password: pwd})
                }).then(r => {
                    if (!r.ok) { showToast('密码错误', 'error'); return; }
                    return r.json();
                }).then(data => {
                    if (!data) return;
                    token = data.token;
                    document.getElementById('loginForm').style.display = 'none';
                    document.getElementById('mainContent').classList.remove('hidden');
                    refreshStatus();
                }).catch(e => {
                    console.error('登录失败:', e);
                    showToast('登录失败: ' + e.message, 'error');
                });
            }
            async function apiCall(path, method='GET', body=null) {
                const opts = { method, headers: { 'Authorization': 'Bearer ' + token } };
                if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
                const r = await fetch(path, opts);
                if (!r.ok) { showToast('API 错误: ' + r.status, 'error'); return null; }
                return r.json();
            }
            async function refreshStatus() {
                const data = await apiCall('/api/status');
                if (!data) return;
                document.getElementById('stats').innerHTML =
                    '<div class="stat-box"><div class="num">'+data.total+'</div><div class="label">总计</div></div>'
                    + '<div class="stat-box"><div class="num">'+data.running+'</div><div class="label">运行中</div></div>'
                    + '<div class="stat-box"><div class="num">'+data.paused+'</div><div class="label">已暂停</div></div>';
                let html = '<table><tr><th>容器名</th><th>状态</th><th>空闲时间</th><th>操作</th></tr>';
                for (const c of data.containers) {
                    html += '<tr><td><strong>'+c.name+'</strong></td>'
                        + '<td><span class="status-badge status-'+c.status.replace(' ','_')+'">'+c.status+'</span></td>'
                        + '<td style="color:#64748b;font-size:13px;">'+c.idle_seconds+'s</td><td>';
                    if (c.status === 'paused') html += '<button class="btn btn-success" onclick="unpause(\''+c.name+'\')">唤醒</button>';
                    else if (c.status === 'running') html += '<button class="btn btn-warning" onclick="pause(\''+c.name+'\')">暂停</button>';
                    html += '</td></tr>';
                }
                html += '</table>';
                document.getElementById('containerList').innerHTML = html;
            }
            async function pause(name) { await apiCall('/api/pause/'+name, 'POST'); refreshStatus(); }
            async function unpause(name) { await apiCall('/api/unpause/'+name, 'POST'); refreshStatus(); }
        </script>
    </body>
    </html>
    """)
    return resp

@app.route("/api/login", methods=["POST"])
def api_login():
    """登录接口，验证密码并返回 token"""
    data = request.get_json()
    if not data or data.get("password") != ADMIN_PASSWORD:
        return jsonify({"error": "密码错误"}), 401
    token = hashlib.sha256(f"dpm-{ADMIN_PASSWORD}".encode()).hexdigest()
    return jsonify({"token": token, "success": True})

@app.route("/api/status")
@require_auth
def api_status():
    containers = []
    total = 0
    running = 0
    paused = 0
    for name, state in monitor.container_states.items():
        total += 1
        container_info = get_container_state(name)
        containers.append({
            "name": name,
            "status": container_info["status"],
            "idle_seconds": state["idle_seconds"],
            "is_paused_by_us": state["is_paused_by_us"],
        })
        if container_info["status"] == "running":
            running += 1
        elif container_info["status"] == "paused":
            paused += 1
    return jsonify({
        "total": total,
        "running": running,
        "paused": paused,
        "containers": containers,
    })

@app.route("/api/pause/<container_name>", methods=["POST"])
@require_auth
def api_pause(container_name):
    if pause_container(container_name):
        monitor.mark_activity(container_name)
        with monitor.lock:
            if container_name in monitor.container_states:
                monitor.container_states[container_name]["is_paused_by_us"] = True
        monitor._save_state()
        return jsonify({"success": True, "message": f"容器 {container_name} 已暂停"})
    return jsonify({"success": False, "message": f"暂停 {container_name} 失败"}), 400

@app.route("/api/unpause/<container_name>", methods=["POST"])
@require_auth
def api_unpause(container_name):
    if unpause_container(container_name):
        monitor.mark_activity(container_name)
        with monitor.lock:
            if container_name in monitor.container_states:
                monitor.container_states[container_name]["is_paused_by_us"] = False
        monitor._save_state()
        return jsonify({"success": True, "message": f"容器 {container_name} 已唤醒"})
    return jsonify({"success": False, "message": f"唤醒 {container_name} 失败"}), 400

@app.route("/api/start/<container_name>", methods=["POST"])
@require_auth
def api_start(container_name):
    if start_container(container_name):
        monitor.mark_activity(container_name)
        with monitor.lock:
            if container_name in monitor.container_states:
                monitor.container_states[container_name]["is_paused_by_us"] = False
        monitor._save_state()
        return jsonify({"success": True, "message": f"容器 {container_name} 已启动"})
    return jsonify({"success": False, "message": f"启动 {container_name} 失败"}), 400

@app.route("/api/config", methods=["GET", "POST"])
@require_auth
def api_config():
    if request.method == "POST":
        data = request.get_json()
        if data and "containers" in data:
            global CONFIG, MONITORED_CONTAINERS
            CONFIG["containers"] = data["containers"]
            MONITORED_CONTAINERS = data["containers"]
            monitor.update_config(data["containers"])
            config_dir = os.path.dirname(CONFIG_PATH)
            if config_dir:
                os.makedirs(config_dir, exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(CONFIG, f, ensure_ascii=False, indent=2)
            return jsonify({"success": True, "message": "配置已更新"})
        return jsonify({"success": False, "message": "无效的配置"}), 400
    return jsonify({
        "containers": MONITORED_CONTAINERS,
        "idle_timeout": DEFAULT_IDLE_TIMEOUT,
        "check_interval": CHECK_INTERVAL,
    })

@app.route("/api/i18n/<lang>")
def api_i18n(lang):
    i18n = {
        "zh-CN": {
            "title": "Docker Pause Manager",
            "login_title": "管理员登录",
            "password_placeholder": "管理员密码",
            "login_btn": "登录",
            "container_status": "容器状态",
            "config": "配置",
            "refresh": "刷新状态",
            "total": "总计",
            "running": "运行中",
            "paused": "已暂停",
            "container_name": "容器名",
            "status": "状态",
            "idle_time": "空闲时间",
            "actions": "操作",
            "wake": "唤醒",
            "pause": "暂停",
            "config_desc": "在 config.json 中配置监控的容器，或通过 API 动态管理。",
        },
        "en": {
            "title": "Docker Pause Manager",
            "login_title": "Admin Login",
            "password_placeholder": "Admin Password",
            "login_btn": "Login",
            "container_status": "Container Status",
            "config": "Configuration",
            "refresh": "Refresh",
            "total": "Total",
            "running": "Running",
            "paused": "Paused",
            "container_name": "Container",
            "status": "Status",
            "idle_time": "Idle Time",
            "actions": "Actions",
            "wake": "Wake",
            "pause": "Pause",
            "config_desc": "Configure monitored containers in config.json, or manage via API.",
        },
    }
    return jsonify(i18n.get(lang, i18n["en"]))

# ========== 启动 ==========

# 模块加载时初始化（Flask 以模块方式运行时 __name__ != "__main__"）
monitor.update_config(MONITORED_CONTAINERS)
init_packet_counter()

t = threading.Thread(target=monitor_loop, daemon=True)
t.start()

log.info(f"Docker Pause Manager 启动完成，监听 http://{LISTEN_HOST}:{LISTEN_PORT}")
log.info(f"监控 {len(MONITORED_CONTAINERS)} 个容器，空闲超时 {DEFAULT_IDLE_TIMEOUT}s，检查间隔 {CHECK_INTERVAL}s")
if packet_counter:
    log.info(f"AF_PACKET 包嗅探已启动，监听端口 {sorted(packet_counter.ports)}，接口 {NETWORK_INTERFACE}")

if __name__ == "__main__":
    app.run(host=LISTEN_HOST, port=LISTEN_PORT, debug=False)