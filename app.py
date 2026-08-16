#!/usr/bin/env python3
"""Docker Pause Manager - 容器自动休眠/唤醒系统"""
import json, os, time, threading, subprocess, logging, hashlib, secrets, hmac
from functools import wraps
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, make_response, redirect
import requests_unixsocket as _rus

# ===== Config =====
CONFIG_PATH = os.environ.get("CONFIG_PATH", "/app/config.json")
STATE_PATH  = os.environ.get("STATE_PATH", "/app/state.json")
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "5287"))

_unix_session = _rus.Session()
DOCKER_SOCK = "http+unix://%2Fvar%2Frun%2Fdocker.sock"

def _docker(method, path, **kw):
    r = _unix_session.request(method, f"{DOCKER_SOCK}{path}", **kw)
    if r.status_code == 204: return None
    if r.status_code >= 400: raise Exception(f"Docker API {r.status_code}: {r.text[:200]}")
    return r.json()

# ===== Default Config =====
DEFAULT_CONFIG = {
    "admin_password": "admin123",
    "global_idle_timeout": 300,
    "check_interval": 10,
    "containers": {},
    "theme": "light",
    "language": "zh-CN"
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("dpm")

# ===== Config & State =====
def _ensure_file(path, default):
    if os.path.isdir(path): return
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f: json.dump(default, f, ensure_ascii=False, indent=2)

_ensure_file(CONFIG_PATH, DEFAULT_CONFIG)
_ensure_file(STATE_PATH, {})

def load_config():
    try:
        with open(CONFIG_PATH) as f: return json.load(f)
    except: return dict(DEFAULT_CONFIG)

def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def load_state():
    try:
        with open(STATE_PATH) as f: return json.load(f)
    except: return {}

def save_state(st):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)

app = Flask(__name__, template_folder="templates")
CFG = load_config()
ADMIN_PASSWORD = CFG.get("admin_password", "admin123")
GLOBAL_IDLE = CFG.get("global_idle_timeout", 300)
CHECK_INTERVAL = CFG.get("check_interval", 10)
MONITORED = CFG.get("containers", {})
THEME = CFG.get("theme", "light")
LANG = CFG.get("language", "zh-CN")

# ===== Token =====
def make_token(pwd, expiry=3600):
    ts = int(time.time()) + expiry
    sig = hmac.new(pwd.encode(), str(ts).encode(), hashlib.sha256).hexdigest()
    return f"{ts}.{sig}"

def verify_token(token):
    try:
        ts, sig = token.split(".", 1)
        expected = hmac.new(ADMIN_PASSWORD.encode(), ts.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected): return False
        if int(ts) < time.time(): return False
        return True
    except: return False

# ===== Auth =====
def require_auth(f):
    @wraps(f)
    def decorated(*a, **kw):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token: token = request.args.get("token", "")
        if not token: token = request.cookies.get("dpm_token", "")
        if not verify_token(token):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect("/")
        return f(*a, **kw)
    return decorated

# ===== Docker Helpers =====
def get_container(name):
    try:
        d = _docker("GET", f"/containers/{name}/json")
        return {"id": d["Id"][:12], "name": d["Name"].lstrip("/"),
                "status": d["State"]["Status"], "image": d.get("Config", {}).get("Image", "")}
    except: return None

def list_containers():
    try:
        data = _docker("GET", "/containers/json?all=1") or []
        result = []
        for c in data:
            name = (c.get("Names") or [""])[0].lstrip("/") or c["Id"][:12]
            ports = sorted(set(
                f"{p.get('PublicPort') or p.get('PrivatePort', 0)}/{p.get('Type', 'tcp')}"
                for p in (c.get("Ports") or []) if p.get("PublicPort") or p.get("PrivatePort")
            ))
            result.append({"name": name, "id": c["Id"][:12], "status": c.get("State", ""),
                           "ports": ports, "image": c.get("Image", "")})
        return result
    except: return []

def pause_container(name):
    try:
        _docker("POST", f"/containers/{name}/pause")
        log.info(f"[{name}] paused")
        return True
    except Exception as e:
        log.warning(f"[{name}] pause failed: {e}")
        return False

def unpause_container(name):
    try:
        _docker("POST", f"/containers/{name}/unpause")
        log.info(f"[{name}] unpaused")
        return True
    except Exception as e:
        log.warning(f"[{name}] unpause failed: {e}")
        return False

# ===== Connection Detection =====
def has_new_connection(ports):
    """检测是否有新连接（包括 SYN_RECV 等初始状态的连接）"""
    for p in ports:
        port = p.get("port", 80)
        proto = p.get("proto", "tcp")
        try:
            r = subprocess.run(["conntrack", "-L", "-p", proto],
                               capture_output=True, text=True, timeout=5)
            for line in r.stdout.split("\n"):
                if not line.strip() or "conntrack" in line.lower():
                    continue
                # 匹配 dport=<port>（目标端口）
                if f"dport={port}" not in line:
                    continue
                # NEW 状态或有 NEW 标记
                if "NEW" in line:
                    return True
                # SYN_RECV 状态表示新连接正在建立
                if "SYN" in line:
                    return True
            return False
        except: pass
    return False

def get_connection_count(ports):
    """统计活跃连接数（ESTABLISHED 或 ASSURED）"""
    count = 0
    for p in ports:
        port = p.get("port", 80)
        proto = p.get("proto", "tcp")
        try:
            r = subprocess.run(["conntrack", "-L", "-p", proto],
                               capture_output=True, text=True, timeout=5)
            for line in r.stdout.split("\n"):
                if not line.strip() or "conntrack" in line.lower():
                    continue
                if f"dport={port}" not in line:
                    continue
                if "ESTABLISHED" in line or "ASSURED" in line:
                    count += 1
        except: pass
    return count

# ===== Monitor =====
class Monitor:
    def __init__(self):
        self.states = {}  # name -> {"idle_seconds": int, "is_paused": bool, "ports": [...]}
        self.lock = threading.Lock()
        self.running = True
        self._restore_state()

    def _restore_state(self):
        st = load_state()
        for name, info in st.items():
            cfg = MONITORED.get(name)
            if cfg:
                self.states[name] = {"idle_seconds": 0, "is_paused": info.get("is_paused", False),
                                     "is_paused_by_us": info.get("is_paused_by_us", False),
                                     "ports": cfg.get("ports", [])}

    def update_config(self, containers):
        with self.lock:
            # Remove containers no longer monitored
            for name in list(self.states.keys()):
                if name not in containers:
                    del self.states[name]
            # Add/update new containers
            for name, cfg in containers.items():
                if name not in self.states:
                    self.states[name] = {"idle_seconds": 0, "is_paused": False,
                                         "is_paused_by_us": False, "ports": cfg.get("ports", [])}
                else:
                    self.states[name]["ports"] = cfg.get("ports", [])

    def mark_activity(self, name):
        with self.lock:
            if name in self.states:
                self.states[name]["idle_seconds"] = 0

    def _save_state(self):
        st = {}
        for name, s in self.states.items():
            st[name] = {"is_paused_by_us": s.get("is_paused_by_us", False)}
        save_state(st)

    def get_status(self, name):
        c = get_container(name)
        if not c: return {"name": name, "status": "not_found", "idle_seconds": 0, "is_paused_by_us": False}
        with self.lock:
            s = self.states.get(name, {})
            return {"name": c["name"], "status": c["status"],
                    "idle_seconds": s.get("idle_seconds", 0),
                    "is_paused_by_us": s.get("is_paused_by_us", False)}

monitor = Monitor()

# ===== Monitor Loop =====
def monitor_loop():
    while monitor.running:
        try:
            with monitor.lock:
                for name, s in list(monitor.states.items()):
                    cfg = MONITORED.get(name)
                    if not cfg: continue
                    idle_timeout = cfg.get("idle_timeout", GLOBAL_IDLE)
                    ports = cfg.get("ports", [])
                    c = get_container(name)
                    if not c: continue

                    if c["status"] == "running" and not s.get("is_paused_by_us"):
                        # Check if has connections
                        if has_new_connection(ports) or get_connection_count(ports) > 0:
                            s["idle_seconds"] = 0
                        else:
                            s["idle_seconds"] = s.get("idle_seconds", 0) + CHECK_INTERVAL
                            if s["idle_seconds"] >= idle_timeout:
                                log.info(f"[{name}] idle {s['idle_seconds']}s >= {idle_timeout}s, pausing")
                                if pause_container(name):
                                    s["is_paused"] = True
                                    s["is_paused_by_us"] = True
                                    s["idle_seconds"] = 0
                                    monitor._save_state()

                    elif c["status"] == "paused" and s.get("is_paused_by_us"):
                        # Check for wake signal
                        if has_new_connection(ports):
                            log.info(f"[{name}] new connection detected, unpausing")
                            if unpause_container(name):
                                s["is_paused"] = False
                                s["is_paused_by_us"] = False
                                s["idle_seconds"] = 0
                                monitor._save_state()

            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            log.error(f"monitor loop error: {e}")
            time.sleep(CHECK_INTERVAL)

# ===== Routes =====
@app.route("/")
def index():
    token = request.args.get("token", "") or request.cookies.get("dpm_token", "")
    if verify_token(token):
        return render_template("index.html", lang=CFG.get("language", "zh-CN"),
                               theme=CFG.get("theme", "light"), token=token)
    return render_template("index.html", lang="zh-CN", theme="light", token="")

@app.route("/login", methods=["POST"])
def web_login():
    password = request.form.get("password", "")
    if password == ADMIN_PASSWORD:
        token = make_token(password)
        resp = make_response(redirect(f"/?token={token}"))
        resp.set_cookie("dpm_token", token, max_age=3600, httponly=False, samesite="Lax")
        return resp
    return redirect("/?error=1")

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    if data.get("password") == ADMIN_PASSWORD:
        token = make_token(ADMIN_PASSWORD)
        return jsonify({"token": token, "success": True})
    return jsonify({"error": "wrong password"}), 401

@app.route("/api/status")
@require_auth
def api_status():
    containers = []
    total = running = paused = 0
    for name in MONITORED:
        total += 1
        st = monitor.get_status(name)
        containers.append(st)
        if st["status"] == "running": running += 1
        elif st["status"] == "paused": paused += 1
    return jsonify({"total": total, "running": running, "paused": paused, "containers": containers})

@app.route("/api/containers")
@require_auth
def api_containers():
    return jsonify({"containers": list_containers()})

@app.route("/api/config", methods=["GET", "POST"])
@require_auth
def api_config():
    global CFG, ADMIN_PASSWORD, GLOBAL_IDLE, CHECK_INTERVAL, MONITORED, THEME, LANG
    if request.method == "POST":
        data = request.get_json() or {}
        if "containers" in data:
            MONITORED = data["containers"]
            CFG["containers"] = data["containers"]
            monitor.update_config(data["containers"])
        if "global_idle_timeout" in data:
            GLOBAL_IDLE = int(data["global_idle_timeout"])
            CFG["global_idle_timeout"] = GLOBAL_IDLE
        if "check_interval" in data:
            CHECK_INTERVAL = int(data["check_interval"])
            CFG["check_interval"] = CHECK_INTERVAL
        if "admin_password" in data:
            ADMIN_PASSWORD = data["admin_password"]
            CFG["admin_password"] = data["admin_password"]
        if "theme" in data:
            THEME = data["theme"]
            CFG["theme"] = data["theme"]
        if "language" in data:
            LANG = data["language"]
            CFG["language"] = data["language"]
        save_config(CFG)
        return jsonify({"success": True})
    return jsonify({
        "containers": MONITORED,
        "global_idle_timeout": GLOBAL_IDLE,
        "check_interval": CHECK_INTERVAL,
        "theme": THEME,
        "language": LANG
    })

@app.route("/api/pause/<name>", methods=["POST"])
@require_auth
def api_pause(name):
    if pause_container(name):
        monitor.mark_activity(name)
        with monitor.lock:
            if name in monitor.states:
                monitor.states[name]["is_paused_by_us"] = True
        monitor._save_state()
        return jsonify({"success": True})
    return jsonify({"success": False}), 400

@app.route("/api/unpause/<name>", methods=["POST"])
@require_auth
def api_unpause(name):
    if unpause_container(name):
        monitor.mark_activity(name)
        with monitor.lock:
            if name in monitor.states:
                monitor.states[name]["is_paused_by_us"] = False
        monitor._save_state()
        return jsonify({"success": True})
    return jsonify({"success": False}), 400

@app.route("/api/i18n")
def api_i18n():
    return jsonify({
        "zh-CN": {
            "title": "Docker 自动休眠管理器",
            "login_title": "管理员登录",
            "password": "密码",
            "login_btn": "登录",
            "wrong_password": "密码错误",
            "total": "总计",
            "running": "运行中",
            "paused": "已休眠",
            "container_status": "容器状态",
            "container_name": "容器名",
            "status": "状态",
            "idle_time": "空闲时间",
            "action": "操作",
            "wake": "唤醒",
            "pause": "休眠",
            "monitor_config": "监控配置",
            "add_container": "添加容器",
            "edit_container": "编辑容器",
            "delete_container": "删除",
            "confirm_delete": "确定删除",
            "no_containers": "暂无监控容器",
            "select_container": "从列表中选择",
            "load_failed": "加载失败",
            "port_config": "端口配置 (每行一个，格式: 端口/协议)",
            "auto_fill": "自动填充",
            "clear_ports": "清空",
            "idle_timeout": "空闲超时 (秒)",
            "check_interval": "检查间隔 (秒)",
            "save": "保存",
            "cancel": "取消",
            "settings": "设置",
            "global_timeout": "全局空闲超时 (秒)",
            "change_password": "修改密码",
            "current_password": "当前密码",
            "new_password": "新密码",
            "confirm_password": "确认密码",
            "theme_label": "主题",
            "light": "日间模式",
            "dark": "夜间模式",
            "language": "语言",
            "refresh": "刷新",
            "saved": "已保存",
            "password_changed": "密码已修改",
            "password_mismatch": "两次密码不一致",
            "wrong_current": "当前密码错误",
            "session_expired": "会话已过期，请重新登录",
            "api_error": "请求失败",
            "port_error": "端口格式错误",
            "fill_name_and_port": "请填写容器名和端口",
            "fill_name": "请填写容器名",
            "fill_port": "请填写端口",
            "logout": "退出登录",
        },
        "en": {
            "title": "Docker Pause Manager",
            "login_title": "Admin Login",
            "password": "Password",
            "login_btn": "Login",
            "wrong_password": "Wrong password",
            "total": "Total",
            "running": "Running",
            "paused": "Paused",
            "container_status": "Container Status",
            "container_name": "Container",
            "status": "Status",
            "idle_time": "Idle Time",
            "action": "Action",
            "wake": "Wake",
            "pause": "Pause",
            "monitor_config": "Monitor Config",
            "add_container": "Add Container",
            "edit_container": "Edit",
            "delete_container": "Delete",
            "confirm_delete": "Are you sure to delete",
            "no_containers": "No containers monitored",
            "select_container": "Select from list",
            "load_failed": "Load failed",
            "port_config": "Ports (one per line, format: port/proto)",
            "auto_fill": "Auto Fill",
            "clear_ports": "Clear",
            "idle_timeout": "Idle Timeout (s)",
            "check_interval": "Check Interval (s)",
            "save": "Save",
            "cancel": "Cancel",
            "settings": "Settings",
            "global_timeout": "Global Idle Timeout (s)",
            "change_password": "Change Password",
            "current_password": "Current Password",
            "new_password": "New Password",
            "confirm_password": "Confirm Password",
            "theme_label": "Theme",
            "light": "Light",
            "dark": "Dark",
            "language": "Language",
            "refresh": "Refresh",
            "saved": "Saved",
            "password_changed": "Password changed",
            "password_mismatch": "Passwords do not match",
            "wrong_current": "Current password is wrong",
            "session_expired": "Session expired, please login again",
            "api_error": "Request failed",
            "port_error": "Invalid port format",
            "fill_name_and_port": "Please fill in name and ports",
            "fill_name": "Please fill in container name",
            "fill_port": "Please fill in ports",
            "logout": "Logout",
        }
    })

# ===== Main =====
log.info("Starting monitor thread...")
monitor.update_config(MONITORED)
t = threading.Thread(target=monitor_loop, daemon=True)
t.start()
log.info(f"Docker Pause Manager started on {LISTEN_HOST}:{LISTEN_PORT}")

if __name__ == "__main__":
    app.run(host=LISTEN_HOST, port=LISTEN_PORT, debug=False)