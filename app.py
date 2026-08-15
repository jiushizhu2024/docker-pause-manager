#!/usr/bin/env python3
"""
Docker Pause Manager - Web UI
自动 pause/unpause Docker 容器，基于 conntrack NEW 连接检测唤醒，空闲超时 pause。
支持多端口容器：一个容器可监控多个 TCP/UDP 端口，任一端口有连接即唤醒，全部端口空闲才 pause。
支持管理员登录认证、自动端口选择。
"""
import json, os, time, threading, subprocess, logging, hashlib, secrets
from functools import wraps
from flask import Flask, request, jsonify, send_file
import docker
try:
    import jwt
except ImportError:
    # 如果没有安装 jwt，动态生成一个简单的 token
    jwt = None

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/app/config.json")
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "5287"))
JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32))
JWT_ALGORITHM = "HS256"
JWT_EXP_HOURS = 24

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pause-manager")

app = Flask(__name__, static_folder=None)
cli = docker.DockerClient(base_url="unix:/var/run/docker.sock")

DEFAULT_CONFIG = {
    "watchers": [],
    "settings": {
        "check_interval": 2,
        "listen_port": 5287,
        "admin_password_hash": ""  # sha256(password + salt)
    }
}

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    save_config(DEFAULT_CONFIG)
    return DEFAULT_CONFIG

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

cfg = load_config()

# 兼容旧配置
for w in cfg.get("watchers", []):
    if "host_port" in w and "host_ports" not in w:
        w["host_ports"] = [{"port": w.pop("host_port"), "protocol": "tcp"}]
    elif "host_ports" not in w:
        w["host_ports"] = []
    else:
        for p in w["host_ports"]:
            if isinstance(p, int):
                idx = w["host_ports"].index(p)
                w["host_ports"][idx] = {"port": p, "protocol": "tcp"}
            elif isinstance(p, dict) and "protocol" not in p:
                p["protocol"] = "tcp"

# 运行时状态
runtime = {}
watchers = {}
watcher_stop_flags = {}

def hash_password(password, salt=None):
    """生成密码哈希"""
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.sha256((password + salt).encode()).hexdigest()
    return f"{salt}:{h}"

def verify_password(password, stored_hash):
    """验证密码"""
    if not stored_hash:
        return False
    try:
        salt, h = stored_hash.split(":")
        return hashlib.sha256((password + salt).encode()).hexdigest() == h
    except Exception:
        return False

def find_free_port(start=5287, end=65535):
    """寻找未占用的端口"""
    import socket
    for port in range(start, end):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((LISTEN_HOST, port))
                return port
        except OSError:
            continue
    return start  # 兜底

def get_container_info(name):
    try:
        c = cli.containers.get(name)
        ports = {}
        for k, v in (c.attrs.get("NetworkSettings", {}).get("Ports") or {}).items():
            if v:
                port = k.split("/")[0]
                proto = k.split("/")[1] if "/" in k else "tcp"
                host_port = v[0].get("HostPort", "")
                ports[f"{port}/{proto}"] = host_port
        return {
            "name": c.name,
            "status": c.status,
            "image": c.image.tags[0] if c.image.tags else str(c.image.id[:12]),
            "ports": ports,
            "short_id": c.short_id,
        }
    except Exception:
        return None

def count_conntrack(port_protos, want_new=True, want_estab=True):
    if not port_protos:
        return 0, 0
    try:
        out = subprocess.run(
            ["conntrack", "-L"],
            capture_output=True, text=True, timeout=5
        )
        new_count = 0
        estab_count = 0
        for line in out.stdout.splitlines():
            matched = False
            for pp in port_protos:
                p = pp["port"]
                proto = pp.get("protocol", "tcp")
                if f"dport={p}" in line:
                    if proto == "tcp" and ("proto=6" in line or "proto=tcp" in line):
                        matched = True
                    elif proto == "udp" and ("proto=17" in line or "proto=udp" in line):
                        matched = True
                    elif proto == "tcp" and "proto=" not in line:
                        matched = True
                    break
            if not matched:
                continue
            if want_new and "NEW" in line:
                new_count += 1
            if want_estab and ("ESTABLISHED" in line or "[UNREPLIED]" in line):
                estab_count += 1
        return new_count, estab_count
    except Exception:
        return 0, 0

def watcher_loop(container_name, host_ports, idle_seconds, check_interval):
    stop = watcher_stop_flags[container_name]
    idle_counter = 0
    log.info(f"Watcher started: {container_name} ports={host_ports} idle={idle_seconds}s interval={check_interval}s")

    while not stop.is_set():
        try:
            c = cli.containers.get(container_name)
            status = c.status
        except Exception:
            time.sleep(check_interval)
            continue

        new_conns, estab_conns = count_conntrack(host_ports)

        rt = runtime.get(container_name, {})
        rt["new_conns"] = new_conns
        rt["estab_conns"] = estab_conns
        rt["status"] = status

        if status == "paused":
            if new_conns > 0 or estab_conns > 0:
                try:
                    c.unpause()
                    rt["state"] = "running"
                    rt["paused_at"] = None
                    rt["last_action"] = f"unpaused (new={new_conns}, estab={estab_conns})"
                    rt["last_action_time"] = time.time()
                    idle_counter = 0
                    log.info(f"Unpaused {container_name} (new={new_conns}, estab={estab_conns})")
                except Exception as e:
                    log.error(f"Failed to unpause {container_name}: {e}")
        elif status == "exited":
            if new_conns > 0 or estab_conns > 0:
                try:
                    c.start()
                    rt["state"] = "running"
                    rt["paused_at"] = None
                    rt["last_action"] = f"started on demand (new={new_conns}, estab={estab_conns})"
                    rt["last_action_time"] = time.time()
                    idle_counter = 0
                    log.info(f"Started {container_name} on demand (new={new_conns}, estab={estab_conns})")
                except Exception as e:
                    log.error(f"Failed to start {container_name}: {e}")
        elif status == "running":
            if new_conns == 0 and estab_conns == 0:
                idle_counter += check_interval
                rt["state"] = "running"
                rt["idle_since"] = idle_counter
                if idle_counter >= idle_seconds:
                    try:
                        c.pause()
                        rt["state"] = "paused"
                        rt["paused_at"] = time.time()
                        rt["last_action"] = f"paused (idle {idle_counter}s)"
                        rt["last_action_time"] = time.time()
                        idle_counter = 0
                        log.info(f"Paused {container_name} (idle {idle_seconds}s)")
                    except Exception as e:
                        log.error(f"Failed to pause {container_name}: {e}")
            else:
                idle_counter = 0
                rt["idle_since"] = 0
                rt["state"] = "running"

        runtime[container_name] = rt
        stop.wait(check_interval)

    log.info(f"Watcher stopped: {container_name}")

def start_watcher(w):
    name = w["container"]
    if name in watchers and watchers[name].is_alive():
        return False
    ports = w.get("host_ports", [])
    idle = w.get("idle_seconds", 300)
    interval = cfg.get("settings", {}).get("check_interval", 2)
    watcher_stop_flags[name] = threading.Event()
    t = threading.Thread(target=watcher_loop, args=(name, ports, idle, interval), daemon=True)
    t.start()
    watchers[name] = t
    return True

def stop_watcher(name):
    if name in watcher_stop_flags:
        watcher_stop_flags[name].set()
    if name in watchers:
        watchers[name].join(timeout=5)
        del watchers[name]
    if name in watcher_stop_flags:
        del watcher_stop_flags[name]
    if name in runtime:
        del runtime[name]

def restart_all_watchers():
    for name in list(watchers.keys()):
        stop_watcher(name)
    for w in cfg.get("watchers", []):
        if w.get("enabled", True):
            start_watcher(w)

# JWT 认证
def generate_token():
    if jwt:
        payload = {"exp": time.time() + JWT_EXP_HOURS * 3600, "admin": True}
        return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    else:
        # 简单 token：随机字符串
        return secrets.token_urlsafe(32)

def verify_token(token):
    if not token:
        return False
    if jwt:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            return payload.get("admin", False)
        except Exception:
            return False
    else:
        # 简单 token：检查是否在内存中（重启后失效）
        return token in valid_tokens

valid_tokens = set()

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not verify_token(token):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# ─── i18n Support ───
I18N_DIR = os.path.join(os.path.dirname(__file__), "i18n")
SUPPORTED_LANGS = ["zh-CN", "en-US"]
DEFAULT_LANG = "zh-CN"

def get_translation(lang):
    """Load translation file for given language"""
    if lang not in SUPPORTED_LANGS:
        lang = DEFAULT_LANG
    path = os.path.join(I18N_DIR, f"{lang}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def get_current_lang(request):
    """Determine current language from request"""
    lang = request.args.get("lang", DEFAULT_LANG)
    if lang in SUPPORTED_LANGS:
        return lang
    accept_lang = request.headers.get("Accept-Language", "")
    for l in SUPPORTED_LANGS:
        if l in accept_lang:
            return l
    return DEFAULT_LANG

# ─── Flask 路由 ───

@app.route("/api/i18n/<lang>")
def get_i18n(lang):
    """Get translation for given language"""
    if lang not in SUPPORTED_LANGS:
        lang = DEFAULT_LANG
    translations = get_translation(lang)
    return jsonify(translations)

@app.route("/api/languages")
def list_languages():
    """List supported languages"""
    return jsonify({
        "languages": SUPPORTED_LANGS,
        "default": DEFAULT_LANG
    })

@app.route("/")
def index():
    return send_file(os.path.join(os.path.dirname(__file__), "static", "index.html"))

@app.route("/api/login", methods=["POST"])
def login():
    data = request.json
    password = data.get("password", "")
    stored_hash = cfg.get("settings", {}).get("admin_password_hash", "")
    
    if not stored_hash:
        # 首次登录：设置密码
        new_hash = hash_password(password)
        cfg["settings"]["admin_password_hash"] = new_hash
        save_config(cfg)
        token = generate_token()
        valid_tokens.add(token)
        return jsonify({"ok": True, "token": token, "first_login": True})
    
    if verify_password(password, stored_hash):
        token = generate_token()
        valid_tokens.add(token)
        return jsonify({"ok": True, "token": token, "first_login": False})
    
    return jsonify({"error": "Invalid password"}), 401

@app.route("/api/logout", methods=["POST"])
@require_auth
def logout():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    valid_tokens.discard(token)
    return jsonify({"ok": True})

@app.route("/api/auth/status")
def auth_status():
    """检查是否已设置密码"""
    has_password = bool(cfg.get("settings", {}).get("admin_password_hash", ""))
    return jsonify({"has_password": has_password})

@app.route("/api/containers")
@require_auth
def list_containers():
    cs = cli.containers.list(all=True)
    result = []
    for c in cs:
        ports = {}
        for k, v in (c.attrs.get("NetworkSettings", {}).get("Ports") or {}).items():
            if v:
                port = k.split("/")[0]
                proto = k.split("/")[1] if "/" in k else "tcp"
                host_port = v[0].get("HostPort", "")
                ports[f"{port}/{proto}"] = host_port
        result.append({
            "name": c.name,
            "status": c.status,
            "image": c.image.tags[0] if c.image.tags else str(c.image.id[:12]),
            "ports": ports,
            "short_id": c.short_id,
        })
    return jsonify(result)

@app.route("/api/watchers")
@require_auth
def list_watchers():
    result = []
    for w in cfg.get("watchers", []):
        name = w["container"]
        rt = runtime.get(name, {})
        ci = get_container_info(name) or {}
        result.append({
            "container": name,
            "host_ports": w.get("host_ports", []),
            "idle_seconds": w.get("idle_seconds", 300),
            "enabled": w.get("enabled", True),
            "image": ci.get("image", ""),
            "short_id": ci.get("short_id", ""),
            "docker_status": ci.get("status", "not-found"),
            "runtime_state": rt.get("state", "unknown"),
            "idle_since": rt.get("idle_since", 0),
            "new_conns": rt.get("new_conns", 0),
            "estab_conns": rt.get("estab_conns", 0),
            "paused_at": rt.get("paused_at"),
            "last_action": rt.get("last_action", ""),
            "last_action_time": rt.get("last_action_time"),
            "watcher_alive": name in watchers and watchers[name].is_alive(),
        })
    return jsonify(result)

@app.route("/api/watchers", methods=["POST"])
@require_auth
def add_watcher():
    data = request.json
    name = data.get("container")
    if not name:
        return jsonify({"error": "container is required"}), 400
    for w in cfg["watchers"]:
        if w["container"] == name:
            return jsonify({"error": "watcher for this container already exists"}), 409
    if "host_ports" in data:
        ports = []
        for p in data["host_ports"]:
            if isinstance(p, dict):
                ports.append({"port": int(p["port"]), "protocol": p.get("protocol", "tcp")})
            elif isinstance(p, int):
                ports.append({"port": p, "protocol": "tcp"})
    else:
        ports = [{"port": int(data["host_port"]), "protocol": "tcp"}] if data.get("host_port") else []
    w = {
        "container": name,
        "host_ports": ports,
        "idle_seconds": int(data.get("idle_seconds", 300)),
        "enabled": data.get("enabled", True),
    }
    cfg["watchers"].append(w)
    save_config(cfg)
    if w["enabled"]:
        start_watcher(w)
    return jsonify({"ok": True, "watcher": w}), 201

@app.route("/api/watchers/<name>", methods=["PUT"])
@require_auth
def update_watcher(name):
    data = request.json
    for i, w in enumerate(cfg["watchers"]):
        if w["container"] == name:
            if "host_ports" in data:
                ports = []
                for p in data["host_ports"]:
                    if isinstance(p, dict):
                        ports.append({"port": int(p["port"]), "protocol": p.get("protocol", "tcp")})
                    elif isinstance(p, int):
                        ports.append({"port": p, "protocol": "tcp"})
                w["host_ports"] = ports
            elif "host_port" in data:
                w["host_ports"] = [{"port": int(data["host_port"]), "protocol": "tcp"}]
            if "idle_seconds" in data:
                w["idle_seconds"] = int(data["idle_seconds"])
            if "enabled" in data:
                w["enabled"] = data["enabled"]
            save_config(cfg)
            if w["enabled"]:
                stop_watcher(name)
                start_watcher(w)
            else:
                stop_watcher(name)
            return jsonify({"ok": True, "watcher": w})
    return jsonify({"error": "not found"}), 404

@app.route("/api/watchers/<name>", methods=["DELETE"])
@require_auth
def delete_watcher(name):
    before = len(cfg["watchers"])
    cfg["watchers"] = [w for w in cfg["watchers"] if w["container"] != name]
    if len(cfg["watchers"]) == before:
        return jsonify({"error": "not found"}), 404
    save_config(cfg)
    stop_watcher(name)
    return jsonify({"ok": True})

@app.route("/api/watchers/<name>/pause", methods=["POST"])
@require_auth
def manual_pause(name):
    try:
        c = cli.containers.get(name)
        c.pause()
        if name in runtime:
            runtime[name]["state"] = "paused"
            runtime[name]["paused_at"] = time.time()
            runtime[name]["last_action"] = "manual paused"
            runtime[name]["last_action_time"] = time.time()
        return jsonify({"ok": True, "status": "paused"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/watchers/<name>/unpause", methods=["POST"])
@require_auth
def manual_unpause(name):
    try:
        c = cli.containers.get(name)
        c.unpause()
        if name in runtime:
            runtime[name]["state"] = "running"
            runtime[name]["paused_at"] = None
            runtime[name]["last_action"] = "manual unpaused"
            runtime[name]["last_action_time"] = time.time()
        return jsonify({"ok": True, "status": "running"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/watchers/<name>/start", methods=["POST"])
@require_auth
def manual_start(name):
    try:
        c = cli.containers.get(name)
        c.start()
        if name in runtime:
            runtime[name]["state"] = "running"
            runtime[name]["paused_at"] = None
            runtime[name]["last_action"] = "manual started"
            runtime[name]["last_action_time"] = time.time()
        return jsonify({"ok": True, "status": "running"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/settings", methods=["GET", "POST"])
@require_auth
def settings():
    if request.method == "POST":
        data = request.json
        if "check_interval" in data:
            cfg["settings"]["check_interval"] = int(data["check_interval"])
        if "listen_port" in data:
            cfg["settings"]["listen_port"] = int(data["listen_port"])
        if "new_password" in data and data["new_password"]:
            cfg["settings"]["admin_password_hash"] = hash_password(data["new_password"])
        save_config(cfg)
        restart_all_watchers()
        return jsonify({"ok": True, "settings": cfg["settings"]})
    # 返回设置（不包含密码哈希）
    s = cfg["settings"].copy()
    s["admin_password_hash"] = "****" if s.get("admin_password_hash") else ""
    return jsonify(s)

@app.route("/api/logs")
@require_auth
def get_logs():
    log_path = "/var/log/docker-pause-manager.log"
    try:
        with open(log_path) as f:
            lines = f.readlines()[-100:]
        return jsonify({"lines": lines})
    except Exception:
        return jsonify({"lines": []})

# ─── 启动 ───

def init():
    # 自动检测端口
    if "listen_port" not in cfg["settings"] or not cfg["settings"]["listen_port"]:
        cfg["settings"]["listen_port"] = find_free_port()
        save_config(cfg)
    else:
        global LISTEN_PORT
        LISTEN_PORT = cfg["settings"]["listen_port"]
    
    # 启动时重启 exited 容器
    for w in cfg.get("watchers", []):
        name = w["container"]
        try:
            c = cli.containers.get(name)
            if c.status == "exited" and w.get("enabled", True):
                c.start()
                log.info(f"Auto-started {name} (was exited)")
        except Exception as e:
            log.warning(f"Failed to auto-start {name}: {e}")
    
    for w in cfg.get("watchers", []):
        if w.get("enabled", True):
            start_watcher(w)

init()

if __name__ == "__main__":
    port = cfg["settings"].get("listen_port", LISTEN_PORT)
    app.run(host=LISTEN_HOST, port=port, debug=False, threaded=True)