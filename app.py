#!/usr/bin/env python3
"""
Docker Pause Manager - Web UI
自动 pause/unpause Docker 容器，基于 AF_PACKET 包嗅探检测流量，空闲超时 pause。
支持多端口容器、管理员登录认证、Web UI 配置管理。
"""
import json, os, time, threading, subprocess, logging, hashlib, secrets, socket as socketlib, struct, re
from functools import wraps
from flask import Flask, request, jsonify, send_file, send_from_directory, make_response

# ===== Docker SDK 兼容性修复 =====
import requests_unixsocket as _rus
import requests as _req

_unix_session = _rus.Session()
DOCKER_SOCKET = "unix://%2Fvar%2Frun%2Fdocker.sock"

def _docker_api(method, path, **kwargs):
    url = f"http+unix://%2Fvar%2Frun%2Fdocker.sock{path}"
    r = _unix_session.request(method, url, **kwargs)
    if r.status_code == 204:
        return None
    if r.status_code >= 400:
        raise Exception(f"Docker API error {r.status_code}: {r.text}")
    return r.json()

def _docker_api_stream(method, path, **kwargs):
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

def get_containers():
    """获取所有容器列表"""
    try:
        data = _docker_api("GET", "/containers/json?all=true")
        containers = []
        for c in data:
            containers.append({
                "id": c["Id"],
                "short_id": c["Id"][:12],
                "name": c["Names"][0].lstrip("/"),
                "status": c["State"],
                "status_raw": c["Status"],
                "image": c["Image"],
                "ports": c.get("Ports", []),
                "labels": c.get("Labels", {})
            })
        return containers
    except Exception as e:
        log.error(f"获取容器列表失败: {e}")
        return []

def get_container_ports(container_name):
    """获取容器所有端口"""
    try:
        data = _docker_api("GET", f"/containers/{container_name}/json")
        ports = []
        host_config = data.get("HostConfig", {})
        port_bindings = host_config.get("PortBindings", {}) or {}
        exposed = data.get("Config", {}).get("ExposedPorts", {}) or {}
        
        # 从 PortBindings 获取端口
        for port_proto, bindings in port_bindings.items():
            port_str, proto = port_proto.split("/")
            port = int(port_str)
            if bindings:
                for b in bindings:
                    host_port = b.get("HostPort", "")
                    if host_port:
                        ports.append({"port": int(host_port), "proto": proto})
                    else:
                        ports.append({"port": int(port), "proto": proto})
            else:
                ports.append({"port": int(port), "proto": proto})
        
        # 从 ExposedPorts 获取未绑定的端口
        for port_proto in exposed:
            port_str, proto = port_proto.split("/")
            port = int(port_str)
            if not any(p["port"] == port and p["proto"] == proto for p in ports):
                ports.append({"port": port, "proto": proto})
        
        return ports
    except Exception as e:
        log.error(f"获取容器端口失败 {container_name}: {e}")
        return []

def pause_container(container_name):
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

def get_container(container_name):
    try:
        data = _docker_api("GET", f"/containers/{container_name}/json")
        return DockerContainer(
            data["Id"],
            data["Name"].lstrip("/"),
            data["State"]["Status"]
        )
    except Exception:
        return None

def get_container_state(container_name):
    container = get_container(container_name)
    if not container:
        return {"status": "not_found", "name": container_name}
    return {
        "name": container.name,
        "status": container.status,
        "id": container.id
    }

# ===== 配置文件管理 =====

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/app/config.json")
STATE_PATH = os.environ.get("STATE_PATH", "/app/state.json")

# 默认配置
DEFAULT_CONFIG = {
    "admin_password": "admin123",
    "idle_timeout": 300,
    "check_interval": 10,
    "net_interface": "eth0",
    "theme": "light",
    "language": "zh-CN",
    "containers": {}
}

LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "5287"))
JWT_SECRET = secrets.token_hex(32)
JWT_ALGORITHM = "HS256"
JWT_EXP_HOURS = 24

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pause-manager")

def ensure_config_file(path, default_content):
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

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_config(cfg):
    dir_name = os.path.dirname(CONFIG_PATH)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

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
    ETHERTYPE_IP = 0x0800
    PROTO_TCP = 6
    PROTO_UDP = 17
    
    def __init__(self, interface, ports):
        self.interface = interface
        self.ports = set(ports)
        self.packet_counts = {}
        self.lock = threading.Lock()
        self.running = False
        self.thread = None
        self.sock = None
        self.interfaces = []
    
    def start(self):
        if self.running:
            return
        self.interfaces = self._discover_interfaces()
        log.info(f"[AF_PACKET] 接口列表: {self.interfaces}")
        
        self.running = True
        self.sock = None
        self.thread = threading.Thread(target=self._rx_loop, daemon=True)
        self.thread.start()
        time.sleep(1)
        if self.thread.is_alive():
            log.info(f"[AF_PACKET] 启动包嗅探，监听 {self.interfaces}，端口 {sorted(self.ports)}")
        else:
            log.error("[AF_PACKET] 启动失败")
    
    def _discover_interfaces(self):
        interfaces = []
        try:
            interfaces.append(self.interface)
            try:
                import subprocess
                result = subprocess.run(["ip", "link", "show"], capture_output=True, text=True, timeout=5)
                for line in result.stdout.split("\n"):
                    if line.strip().startswith("br-") and ": <" in line:
                        iface = line.split(":")[0].strip()
                        interfaces.append(iface)
            except (FileNotFoundError, subprocess.TimeoutExpired):
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
        return list(set(interfaces))
    
    def stop(self):
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
        import threading
        
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
        
        threads = []
        for iface in self.interfaces:
            t = threading.Thread(target=_listen_iface, args=(iface,), daemon=True)
            t.start()
            threads.append(t)
        
        while self.running:
            time.sleep(1)
    
    def _parse_packet(self, data):
        if len(data) < 34:
            return
        eth_type = struct.unpack('!H', data[12:14])[0]
        if eth_type != self.ETHERTYPE_IP:
            return
        
        ip_header = data[14:]
        if len(ip_header) < 20:
            return
        
        proto = ip_header[9]
        
        if proto == self.PROTO_TCP:
            if len(ip_header) < 32:
                return
            src_port = struct.unpack('!H', ip_header[20:22])[0]
            dst_port = struct.unpack('!H', ip_header[22:24])[0]
            self._count_port(src_port)
            self._count_port(dst_port)
        
        elif proto == self.PROTO_UDP:
            if len(ip_header) < 28:
                return
            src_port = struct.unpack('!H', ip_header[20:22])[0]
            dst_port = struct.unpack('!H', ip_header[22:24])[0]
            self._count_port(src_port)
            self._count_port(dst_port)
    
    def _count_port(self, port):
        if port in self.ports:
            with self.lock:
                self.packet_counts[port] = self.packet_counts.get(port, 0) + 1
    
    def get_count(self, port):
        with self.lock:
            return self.packet_counts.get(port, 0)
    
    def reset_count(self, port):
        with self.lock:
            self.packet_counts[port] = 0

packet_counter = None

def init_packet_counter():
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
    return token == hashlib.sha256(f"dpm-{ADMIN_PASSWORD}".encode()).hexdigest()

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.args.get("token")
        if not token or not verify_token(token):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json()
    if not data or data.get("password") != ADMIN_PASSWORD:
        return jsonify({"error": "密码错误"}), 401
    token = hashlib.sha256(f"dpm-{ADMIN_PASSWORD}".encode()).hexdigest()
    return jsonify({"success": True, "token": token, "password": ADMIN_PASSWORD})

@app.route("/api/status")
@token_required
def api_status():
    result = {"containers": [], "paused": 0, "running": 0, "total": 0}
    for name, state in monitor.container_states.items():
        container = get_container_state(name)
        entry = {
            "name": name,
            "status": container["status"],
            "idle_seconds": state["idle_seconds"],
            "idle_timeout": state["idle_timeout"],
            "is_paused_by_us": state["is_paused_by_us"],
            "ports": state["ports"]
        }
        result["containers"].append(entry)
        result["total"] += 1
        if state["is_paused_by_us"]:
            result["paused"] += 1
        else:
            result["running"] += 1
    return jsonify(result)

@app.route("/api/pause/<container_name>", methods=["POST"])
@token_required
def api_pause(container_name):
    if pause_container(container_name):
        if container_name in monitor.container_states:
            monitor.container_states[container_name]["is_paused_by_us"] = True
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "容器不存在或未运行"}), 404

@app.route("/api/unpause/<container_name>", methods=["POST"])
@token_required
def api_unpause(container_name):
    if unpause_container(container_name):
        if container_name in monitor.container_states:
            monitor.container_states[container_name]["is_paused_by_us"] = False
            monitor.container_states[container_name]["idle_seconds"] = 0
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "容器未暂停"}), 404

@app.route("/api/start/<container_name>", methods=["POST"])
@token_required
def api_start(container_name):
    if start_container(container_name):
        if container_name in monitor.container_states:
            monitor.container_states[container_name]["is_paused_by_us"] = False
            monitor.container_states[container_name]["idle_seconds"] = 0
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "容器不存在或未退出"}), 404

@app.route("/api/containers")
@token_required
def api_list_containers():
    """获取所有 Docker 容器"""
    containers = get_containers()
    # 过滤掉已监控的容器，并添加监控状态
    result = []
    for c in containers:
        is_monitored = c["name"] in MONITORED_CONTAINERS
        result.append({
            "name": c["name"],
            "status": c["status"],
            "ports": c["ports"],
            "monitored": is_monitored
        })
    return jsonify(result)

@app.route("/api/ports/<container_name>")
@token_required
def api_container_ports(container_name):
    """获取容器的所有端口"""
    ports = get_container_ports(container_name)
    return jsonify(ports)

@app.route("/api/add_container", methods=["POST"])
@token_required
def api_add_container():
    """添加容器到监控"""
    data = request.get_json()
    if not data:
        return jsonify({"error": "无效请求"}), 400
    
    container_name = data.get("name")
    ports = data.get("ports", [])
    idle_timeout = data.get("idle_timeout", DEFAULT_IDLE_TIMEOUT)
    
    if not container_name or not ports:
        return jsonify({"error": "容器名和端口不能为空"}), 400
    
    # 检查容器是否存在
    container = get_container(container_name)
    if not container:
        return jsonify({"error": f"容器 {container_name} 不存在"}), 404
    
    # 添加或更新配置
    cfg_entry = {
        "ports": ports,
        "idle_timeout": int(idle_timeout) if idle_timeout else DEFAULT_IDLE_TIMEOUT
    }
    
    MONITORED_CONTAINERS[container_name] = cfg_entry
    save_config({"admin_password": ADMIN_PASSWORD, "idle_timeout": DEFAULT_IDLE_TIMEOUT,
                 "check_interval": CHECK_INTERVAL, "net_interface": NETWORK_INTERFACE,
                 "theme": CONFIG.get("theme", "light"), "language": CONFIG.get("language", "zh-CN"),
                 "containers": MONITORED_CONTAINERS})
    
    # 更新监控器
    monitor.update_config(MONITORED_CONTAINERS)
    
    # 重新初始化包计数器
    init_packet_counter()
    
    log.info(f"[{container_name}] 已添加到监控，端口: {ports}")
    return jsonify({"success": True, "message": f"已添加 {container_name}"})

@app.route("/api/remove_container", methods=["POST"])
@token_required
def api_remove_container():
    """从监控中移除容器，先唤醒再删除"""
    data = request.get_json()
    container_name = data.get("name")

    if not container_name:
        return jsonify({"error": "容器名不能为空"}), 400

    # 先唤醒容器（无论当前状态）
    container = get_container(container_name)
    if container:
        if container.status == "paused":
            unpause_container(container_name)
            log.info(f"[{container_name}] 删除前已唤醒")
        elif container.status == "exited":
            start_container(container_name)
            log.info(f"[{container_name}] 删除前已启动")

    # 从监控配置中删除
    if container_name in MONITORED_CONTAINERS:
        del MONITORED_CONTAINERS[container_name]
    if container_name in monitor.container_states:
        del monitor.container_states[container_name]

    # 保存配置
    save_config({"admin_password": ADMIN_PASSWORD, "idle_timeout": DEFAULT_IDLE_TIMEOUT,
                 "check_interval": CHECK_INTERVAL, "net_interface": NETWORK_INTERFACE,
                 "theme": CONFIG.get("theme", "light"), "language": CONFIG.get("language", "zh-CN"),
                 "containers": MONITORED_CONTAINERS})
    init_packet_counter()
    log.info(f"[{container_name}] 已从监控中移除")

    return jsonify({"success": True})

@app.route("/api/settings", methods=["GET", "PUT"])
def api_settings():
    """获取或更新全局设置"""
    global ADMIN_PASSWORD, DEFAULT_IDLE_TIMEOUT, CHECK_INTERVAL, NETWORK_INTERFACE
    
    if request.method == "GET":
        return jsonify({
            "admin_password": ADMIN_PASSWORD,
            "idle_timeout": DEFAULT_IDLE_TIMEOUT,
            "check_interval": CHECK_INTERVAL,
            "net_interface": NETWORK_INTERFACE,
            "theme": CONFIG.get("theme", "light"),
            "language": CONFIG.get("language", "zh-CN")
        })
    
    # PUT - 更新设置
    data = request.get_json()
    if not data:
        return jsonify({"error": "无效请求"}), 400
    
    # 验证 token
    token = request.args.get("token")
    if not token or not verify_token(token):
        return jsonify({"error": "Unauthorized"}), 401
    
    if "admin_password" in data:
        ADMIN_PASSWORD = data["admin_password"]
    if "idle_timeout" in data:
        DEFAULT_IDLE_TIMEOUT = int(data["idle_timeout"])
    if "check_interval" in data:
        CHECK_INTERVAL = int(data["check_interval"])
    if "net_interface" in data:
        NETWORK_INTERFACE = data["net_interface"]
    if "theme" in data:
        CONFIG["theme"] = data["theme"]
    if "language" in data:
        CONFIG["language"] = data["language"]
    
    save_config({**CONFIG, "idle_timeout": DEFAULT_IDLE_TIMEOUT, "check_interval": CHECK_INTERVAL,
                 "net_interface": NETWORK_INTERFACE, "containers": MONITORED_CONTAINERS})
    
    log.info(f"设置已更新: theme={CONFIG.get('theme')}, language={CONFIG.get('language')}")
    return jsonify({"success": True})

@app.route("/api/config", methods=["GET", "POST"])
@token_required
def api_config():
    if request.method == "GET":
        return jsonify(MONITORED_CONTAINERS)
    
    data = request.get_json()
    if not data:
        return jsonify({"error": "无效请求"}), 400
    
    MONITORED_CONTAINERS.clear()
    for name, cfg in data.items():
        MONITORED_CONTAINERS[name] = cfg
    
    monitor.update_config(MONITORED_CONTAINERS)
    save_config({"admin_password": ADMIN_PASSWORD, "idle_timeout": DEFAULT_IDLE_TIMEOUT,
                 "check_interval": CHECK_INTERVAL, "net_interface": NETWORK_INTERFACE,
                 "theme": CONFIG.get("theme", "light"), "language": CONFIG.get("language", "zh-CN"),
                 "containers": MONITORED_CONTAINERS})
    init_packet_counter()
    
    return jsonify({"success": True})

@app.route("/api/i18n/<lang>")
@token_required
def api_i18n(lang):
    i18n = {
        "zh-CN": {
            "title": "Docker Pause Manager",
            "pause": "暂停",
            "unpause": "唤醒",
            "start": "启动",
            "settings": "全局设置",
            "containers": "监控容器",
            "add_container": "添加容器",
            "remove": "移除",
            "idle_timeout": "空闲超时",
            "seconds": "秒",
            "global_idle_timeout": "全局空闲超时",
            "theme": "主题",
            "language": "语言",
            "light": "亮色",
            "dark": "暗色",
            "refresh": "刷新",
            "status": "状态",
            "running": "运行中",
            "paused": "已暂停",
            "idle_time": "空闲时间",
            "ports": "端口",
            "select_all": "全选",
            "selected": "已选",
            "auto_add_ports": "自动添加端口",
            "custom_ports": "手动添加端口",
            "port": "端口",
            "proto": "协议",
            "tcp": "TCP",
            "udp": "UDP",
            "add_port": "添加端口",
            "per_container_timeout": "单独超时设置",
            "use_global": "使用全局"
        },
        "en": {
            "title": "Docker Pause Manager",
            "pause": "Pause",
            "unpause": "Unpause",
            "start": "Start",
            "settings": "Settings",
            "containers": "Containers",
            "add_container": "Add Container",
            "remove": "Remove",
            "idle_timeout": "Idle Timeout",
            "seconds": "seconds",
            "global_idle_timeout": "Global Idle Timeout",
            "theme": "Theme",
            "language": "Language",
            "light": "Light",
            "dark": "Dark",
            "refresh": "Refresh",
            "status": "Status",
            "running": "Running",
            "paused": "Paused",
            "idle_time": "Idle Time",
            "ports": "Ports",
            "select_all": "Select All",
            "selected": "selected",
            "auto_add_ports": "Auto Add Ports",
            "custom_ports": "Custom Ports",
            "port": "Port",
            "proto": "Proto",
            "tcp": "TCP",
            "udp": "UDP",
            "add_port": "Add Port",
            "per_container_timeout": "Per-Container Timeout",
            "use_global": "Use Global"
        }
    }
    return jsonify(i18n.get(lang, i18n["en"]))

# ========== 容器监控循环 ==========

class ContainerMonitor:
    def __init__(self):
        self.container_states = {}
        self.lock = threading.Lock()
        self.running = True
        self._load_state()

    def _save_state(self):
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

    def run_check(self):
        with self.lock:
            needs_save = False
            for name, state in self.container_states.items():
                ports = state["ports"]
                idle_timeout = state["idle_timeout"]

                container = get_container(name)
                if not container:
                    continue

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
                        count = packet_counter.get_count(port) if packet_counter else 0
                        if count > 0:
                            has_active = True
                            packet_counter.reset_count(port)
                            break

                    if has_active:
                        state["idle_seconds"] = 0
                    else:
                        state["idle_seconds"] += CHECK_INTERVAL
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

def has_new_connection(container_name, ports):
    global packet_counter
    if packet_counter is None:
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

# ========== Web API ==========

@app.route("/")
def index():
    try:
        return send_from_directory(os.path.dirname(__file__), "templates/index.html")
    except FileNotFoundError:
        return make_response("Template not found", 404)

# ========== 启动 ==========

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
