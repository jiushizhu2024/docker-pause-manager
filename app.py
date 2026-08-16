#!/usr/bin/env python3
"""Docker Pause Manager - 容器自动休眠/唤醒系统 v3 (AF_PACKET 检测)"""
import json, os, time, threading, subprocess, logging, hashlib, hmac, struct, socket
from functools import wraps
from flask import Flask, request, jsonify, render_template, make_response, redirect

# ===== Config =====
CONFIG_PATH = os.environ.get("CONFIG_PATH", "/app/config.json")
STATE_PATH  = os.environ.get("STATE_PATH", "/app/state.json")
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "5287"))

DEFAULT_CONFIG = {
    "admin_password": "admin123",
    "global_idle_timeout": 300,
    "check_interval": 10,
    "net_interface": "eth0",
    "containers": {},
    "theme": "light",
    "language": "zh-CN"
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("dpm")

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

CFG = load_config()
ADMIN_PASSWORD = CFG.get("admin_password", "admin123")
GLOBAL_IDLE = CFG.get("global_idle_timeout", 300)
CHECK_INTERVAL = CFG.get("check_interval", 10)
MONITORED = CFG.get("containers", {})
THEME = CFG.get("theme", "light")
LANG = CFG.get("language", "zh-CN")
NET_IFACE = CFG.get("net_interface", "eth0")

app = Flask(__name__, template_folder="templates")

# ===== Token Auth =====
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

# ===== Docker API =====
import requests_unixsocket as _rus
_unix_session = _rus.Session()
DOCKER_SOCK = "http+unix://%2Fvar%2Frun%2Fdocker.sock"

def _docker(method, path, **kw):
    r = _unix_session.request(method, f"{DOCKER_SOCK}{path}", **kw)
    if r.status_code == 204: return None
    if r.status_code >= 400: raise Exception(f"Docker API {r.status_code}: {r.text[:200]}")
    return r.json()

def get_container(name):
    try:
        info = _docker("GET", f"/containers/{name}/json")
        state = info.get("State", {})
        ports = info.get("Ports", [])
        port_map = {p["PrivatePort"]: p.get("PublicPort", "") for p in ports}
        return {"id": info.get("Id", "")[:12], "name": info.get("Names", [""])[0].lstrip("/"),
                "image": info.get("Image", ""), "status": state.get("Status", ""),
                "ports": port_map}
    except: return None

def list_containers():
    try:
        containers = _docker("GET", "/containers/json", params={"all": True})
        return [{"id": c["Id"][:12], "name": c["Names"][0].lstrip("/"),
                 "image": c["Image"], "status": c["States"],
                 "ports": {p["PrivatePort"]: p.get("PublicPort", "") for p in c.get("Ports", [])}}
                for c in containers]
    except: return []

def pause_container(name):
    try:
        _docker("POST", f"/containers/{name}/pause")
        log.info(f"[{name}] paused"); return True
    except Exception as e:
        log.warning(f"[{name}] pause failed: {e}"); return False

def unpause_container(name):
    try:
        _docker("POST", f"/containers/{name}/unpause")
        log.info(f"[{name}] unpaused"); return True
    except Exception as e:
        log.warning(f"[{name}] unpause failed: {e}"); return False

def stop_container(name):
    try:
        _docker("POST", f"/containers/{name}/stop", params={"t": "5"})
        log.info(f"[{name}] stopped"); return True
    except Exception as e:
        log.warning(f"[{name}] stop failed: {e}"); return False

def start_container(name):
    try:
        _docker("POST", f"/containers/{name}/start")
        log.info(f"[{name}] started"); return True
    except Exception as e:
        log.warning(f"[{name}] start failed: {e}"); return False

# ===== AF_PACKET 包计数器 (Lazytainer 风格) =====
class PacketCounter:
    """使用 AF_PACKET raw socket 持续监听网卡上发往指定端口的数据包，
       维护滑动窗口历史，检测空闲/活动。"""
    def __init__(self):
        self.socks = {}       # name -> {sock, ports, rx_count}
        self.lock = threading.Lock()
        self.rx_history = {}  # name -> [count, ...]
        self.running = True

    def _start_sock(self, name, ports):
        """启动 AF_PACKET socket 并开始抓包"""
        tcp_ports = [p.get("port", 80) for p in ports if p.get("proto", "tcp") in ("tcp", "TCP")]
        udp_ports = [p.get("port", 80) for p in ports if p.get("proto", "udp") in ("udp", "UDP")]
        all_ports = list(set(tcp_ports + udp_ports))
        if not all_ports: return
        try:
            sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
            try:
                sock.bind((NET_IFACE, socket.ntohs(0x0003)))
            except OSError:
                pass  # 绑定接口失败，仍用默认
            sock.settimeout(0.5)
            self.socks[name] = {"sock": sock, "ports": all_ports, "rx_count": 0}
            t = threading.Thread(target=self._rx_loop, args=(name,), daemon=True)
            t.start()
            log.info(f"[{name}] packet counter started, monitoring ports {all_ports}")
        except Exception as e:
            log.warning(f"[{name}] packet counter start failed: {e}")

    def _rx_loop(self, name):
        """持续接收并计数"""
        while self.running and name in self.socks:
            try:
                info = self.socks.get(name)
                if not info: return
                data = info["sock"].recvfrom(65535)[0]
                if len(data) < 34: continue
                ip_header = data[14:34]
                ihl = (ip_header[0] & 0xF) * 4
                if ihl < 20 or len(data) < 14 + ihl + 8: continue
                proto = ip_header[9]
                if proto == 6:   # TCP
                    tcp_h = data[14+ihl:14+ihl+20]
                    dst_port = struct.unpack('>H', tcp_h[2:4])[0]
                elif proto == 17:  # UDP
                    udp_h = data[14+ihl:14+ihl+8]
                    dst_port = struct.unpack('>H', udp_h[2:4])[0]
                else:
                    continue
                if dst_port in info.get("ports", []):
                    info["rx_count"] += 1
            except socket.timeout:
                continue
            except Exception:
                return

    def register(self, name, ports):
        with self.lock:
            if name in self.socks:
                self._stop_sock(name)
            self._start_sock(name, ports)
            self.rx_history[name] = [0] * 3

    def unregister(self, name):
        with self.lock:
            self._stop_sock(name)
            self.rx_history.pop(name, None)

    def _stop_sock(self, name):
        info = self.socks.get(name)
        if info:
            try: info["sock"].close()
            except: pass
            self.socks.pop(name, None)

    def record(self, name):
        """将当前包数追加到历史窗口"""
        with self.lock:
            cur = self.socks.get(name, {}).get("rx_count", 0)
            hist = self.rx_history.get(name, [0])
            self.rx_history[name] = [hist[-1]] + [cur]

    def is_idle(self, name, threshold=10):
        """滑动窗口内包数变化 < threshold = 空闲"""
        with self.lock:
            hist = self.rx_history.get(name)
            if not hist or len(hist) < 2: return True
            diff = hist[-1] - hist[0]
            return diff < threshold

    def shutdown(self):
        self.running = False
        with self.lock:
            for name in list(self.socks.keys()):
                self._stop_sock(name)

packet_counter = PacketCounter()


# ===== Monitor =====
class Monitor:
    def __init__(self):
        self.states = {}
        self.lock = threading.Lock()
        self.running = True
        self._restore_state()

    def _restore_state(self):
        st = load_state()
        for name, info in st.items():
            cfg = MONITORED.get(name)
            if not cfg: continue
            real = get_container(name)
            real_paused = real and real["status"] in ("paused", "exited")
            restored_by_us = info.get("is_paused_by_us", False) and real_paused
            self.states[name] = {"idle_seconds": 0, "is_paused": real_paused,
                                 "is_paused_by_us": restored_by_us,
                                 "ports": cfg.get("ports", [])}
            packet_counter.register(name, self.states[name]["ports"])

    def update_config(self, containers):
        with self.lock:
            for name in list(self.states.keys()):
                if name not in containers:
                    del self.states[name]
                    packet_counter.unregister(name)
            for name, cfg in containers.items():
                if name not in self.states:
                    self.states[name] = {"idle_seconds": 0, "is_paused": False,
                                         "is_paused_by_us": False, "ports": cfg.get("ports", [])}
                    packet_counter.register(name, self.states[name]["ports"])
                else:
                    old = self.states[name].get("ports", [])
                    new = cfg.get("ports", [])
                    if old != new:
                        self.states[name]["ports"] = new
                        packet_counter.register(name, new)

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
            return {"name": name, "status": c["status"], "idle_seconds": s.get("idle_seconds", 0),
                    "is_paused_by_us": s.get("is_paused_by_us", False),
                    "image": c["image"], "ports": c["ports"]}

    def get_all_status(self):
        result = []
        for name in MONITORED:
            c = get_container(name)
            if not c: continue
            with self.lock:
                s = self.states.get(name, {})
            result.append({"name": name, "status": c["status"],
                           "idle_seconds": s.get("idle_seconds", 0),
                           "is_paused_by_us": s.get("is_paused_by_us", False),
                           "image": c["image"], "ports": c["ports"]})
        paused = sum(1 for r in result if r["status"] == "paused")
        running = sum(1 for r in result if r["status"] == "running")
        return {"containers": result, "paused": paused, "running": running, "total": len(result)}

monitor = Monitor()

def monitor_loop():
    while monitor.running:
        try:
            time.sleep(CHECK_INTERVAL)
            if not monitor.running: break
            with monitor.lock:
                for name, s in list(monitor.states.items()):
                    cfg = MONITORED.get(name)
                    if not cfg: continue
                    idle_timeout = cfg.get("idle_timeout", GLOBAL_IDLE)
                    sleep_mode = cfg.get("sleep_mode", "pause")
                    threshold = cfg.get("min_packet_threshold", 10)
                    ports = s.get("ports", [])

                    c = get_container(name)
                    if not c: continue

                    packet_counter.record(name)

                    if c["status"] == "running":
                        if packet_counter.is_idle(name, threshold):
                            s["idle_seconds"] = s.get("idle_seconds", 0) + CHECK_INTERVAL
                            if s["idle_seconds"] >= idle_timeout:
                                log.info(f"[{name}] idle {s['idle_seconds']}s >= {idle_timeout}s, {sleep_mode}ing")
                                ok = pause_container(name) if sleep_mode == "pause" else stop_container(name)
                                if ok:
                                    s["is_paused"] = True
                                    s["is_paused_by_us"] = True
                                    s["idle_seconds"] = 0
                                    monitor._save_state()
                        else:
                            s["idle_seconds"] = 0

                    elif c["status"] == "paused" and s.get("is_paused_by_us"):
                        if not packet_counter.is_idle(name, threshold):
                            log.info(f"[{name}] activity detected (packets > {threshold}), unpausing")
                            ok = unpause_container(name)
                            if ok:
                                s["is_paused"] = False
                                s["is_paused_by_us"] = False
                                s["idle_seconds"] = 0
                                monitor._save_state()
                    elif c["status"] == "exited" and s.get("is_paused_by_us"):
                        if not packet_counter.is_idle(name, threshold):
                            log.info(f"[{name}] activity detected, starting")
                            ok = start_container(name)
                            if ok:
                                s["is_paused"] = False
                                s["is_paused_by_us"] = False
                                s["idle_seconds"] = 0
                                monitor._save_state()
        except Exception as e:
            log.warning(f"Monitor error: {e}")


# ===== API Routes =====
@app.route("/")
def index():
    token = request.args.get("token", "")
    if not verify_token(token): return redirect("/")
    return render_template("index.html", token=token, theme=THEME, lang=LANG)

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    pwd = data.get("password", "")
    if pwd == ADMIN_PASSWORD:
        token = make_token(ADMIN_PASSWORD)
        resp = jsonify({"success": True, "token": token})
        resp.set_cookie("dpm_token", token, max_age=3600)
        return resp
    return jsonify({"success": False, "error": "密码错误"}), 401

@app.route("/api/logout", methods=["POST"])
def logout():
    resp = make_response(jsonify({"success": True}))
    resp.delete_cookie("dpm_token")
    return resp

@app.route("/api/containers")
def api_containers():
    data = []
    for c in list_containers():
        data.append({"id": c["id"], "name": c["name"], "image": c["image"],
                     "status": c["status"], "ports": [{"port": int(k), "proto": "tcp"} for k in c.get("ports", {})]})
    return jsonify({"containers": data})

@app.route("/api/status")
def api_status():
    return jsonify(monitor.get_all_status())

@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify({"admin_password": "******", "global_idle_timeout": GLOBAL_IDLE,
                    "check_interval": CHECK_INTERVAL, "containers": MONITORED,
                    "theme": THEME, "language": LANG, "net_interface": NET_IFACE})

@app.route("/api/config", methods=["POST"])
def save_config_api():
    data = request.get_json(silent=True) or {}
    global ADMIN_PASSWORD, GLOBAL_IDLE, CHECK_INTERVAL, MONITORED, THEME, LANG, NET_IFACE
    if "admin_password" in data and data["admin_password"]:
        ADMIN_PASSWORD = data["admin_password"]
    GLOBAL_IDLE = data.get("global_idle_timeout", GLOBAL_IDLE)
    CHECK_INTERVAL = data.get("check_interval", CHECK_INTERVAL)
    NET_IFACE = data.get("net_interface", NET_IFACE)
    THEME = data.get("theme", THEME)
    LANG = data.get("language", LANG)
    if "containers" in data:
        MONITORED = data["containers"]
        monitor.update_config(MONITORED)
    save_config({"admin_password": ADMIN_PASSWORD, "global_idle_timeout": GLOBAL_IDLE,
                 "check_interval": CHECK_INTERVAL, "containers": MONITORED,
                 "theme": THEME, "language": LANG, "net_interface": NET_IFACE})
    return jsonify({"success": True})

@app.route("/api/password", methods=["POST"])
def change_password():
    data = request.get_json(silent=True) or {}
    if data.get("current_password") != ADMIN_PASSWORD:
        return jsonify({"success": False, "error": "当前密码错误"}), 400
    new = data.get("new_password")
    confirm = data.get("confirm_password")
    if new != confirm:
        return jsonify({"success": False, "error": "两次密码不一致"}), 400
    global ADMIN_PASSWORD
    ADMIN_PASSWORD = new
    save_config({"admin_password": ADMIN_PASSWORD, "global_idle_timeout": GLOBAL_IDLE,
                 "check_interval": CHECK_INTERVAL, "containers": MONITORED,
                 "theme": THEME, "language": LANG, "net_interface": NET_IFACE})
    return jsonify({"success": True})

@app.route("/api/pause/<name>", methods=["POST"])
@require_auth
def api_pause(name):
    cfg = MONITORED.get(name)
    sleep_mode = cfg.get("sleep_mode", "pause") if cfg else "pause"
    if sleep_mode == "pause":
        ok = pause_container(name)
    else:
        ok = stop_container(name)
    with monitor.lock:
        if name in monitor.states:
            monitor.states[name]["is_paused"] = True
            monitor.states[name]["is_paused_by_us"] = True
            monitor.states[name]["idle_seconds"] = 0
            monitor._save_state()
    return jsonify({"success": ok})

@app.route("/api/unpause/<name>", methods=["POST"])
@require_auth
def api_unpause(name):
    c = get_container(name)
    if not c: return jsonify({"success": False, "error": "容器不存在"}), 404
    if c["status"] == "paused": ok = unpause_container(name)
    elif c["status"] == "exited": ok = start_container(name)
    else: ok = True
    with monitor.lock:
        if name in monitor.states:
            monitor.states[name]["is_paused"] = False
            monitor.states[name]["is_paused_by_us"] = False
            monitor.states[name]["idle_seconds"] = 0
            monitor._save_state()
    return jsonify({"success": ok})

@app.route("/api/ping", methods=["GET"])
def api_ping():
    return jsonify({"success": True, "time": time.time()})


# ===== Main =====
log.info("Starting monitor thread...")
monitor.update_config(MONITORED)
t = threading.Thread(target=monitor_loop, daemon=True)
t.start()
log.info(f"Docker Pause Manager v3 (AF_PACKET) started on {LISTEN_HOST}:{LISTEN_PORT}")

if __name__ == "__main__":
    app.run(host=LISTEN_HOST, port=LISTEN_PORT, debug=False)