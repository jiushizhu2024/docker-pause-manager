#!/usr/bin/env python3
"""
docker-pause-manager v4 - 使用 nflog 检测连接唤醒
原理：iptables 将入站流量记录到 nflog，程序监听 nflog netlink socket
"""
import subprocess, threading, time, json, socket as socketlib, struct, os
from pathlib import Path

CONFIG_PATH = "/app/config.json"
STATE_PATH = "/app/state.json"
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 5287
CHECK_INTERVAL = 10
GLOBAL_IDLE = 300

# nflog 配置
NFLOG_GROUP = 100
NFLOG_QUEUE_LEN = 10
NFLOG_BURST = 10
NFLOG_TIMEOUT = 2  # 秒

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

# 配置
with open(CONFIG_PATH) as f:
    CFG = json.load(f)
ADMIN_PASSWORD = CFG.get("admin_password", "admin123")
MONITORED = CFG.get("containers", {})
NET_IFACE = CFG.get("net_interface", "eth0")
GLOBAL_IDLE = CFG.get("global_idle_timeout", 300)
CHECK_INTERVAL = CFG.get("check_interval", 10)


def _docker(method, path, data=None):
    import urllib.request
    import base64
    req = urllib.request.Request(f"unix:/var/run/docker.sock:{method}{path}")
    if data:
        req.data = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except Exception as e:
        log.warning(f"Docker API error: {e}")
        return None


def get_container(name):
    c = _docker("GET", f"/containers/{name}/json")
    if not c: return None
    ports = []
    for k, v in c.get("HostConfig", {}).get("PortBindings", {}).items():
        if v:
            proto, port = k.split("/")
            ports.append({"port": int(v[0]["HostPort"]), "proto": proto})
    return {"name": name, "status": c["State"]["Status"], "ports": ports,
            "image": c.get("Config", {}).get("Image", "")}


def pause_container(name):
    _docker("POST", f"/containers/{name}/pause")
    return True


def unpause_container(name):
    _docker("POST", f"/containers/{name}/unpause")
    return True


def start_container(name):
    _docker("POST", f"/containers/{name}/start")
    return True


def stop_container(name):
    _docker("POST", f"/containers/{name}/stop")
    return True


class NFLogMonitor:
    """监听 nflog 捕获的入站连接"""
    
    def __init__(self):
        self.socks = {}  # name -> {"rx_count": int}
        self.running = True
        self.nl = None
        self._start_nflog()
    
    def _start_nflog(self):
        """启动 nflog netlink socket"""
        try:
            self.nl = socketlib.socket(socketlib.AF_NETLINK, socketlib.SOCK_RAW, 14)  # NETLINK_NETFILTER
            self.nl.bind((0, NFLOG_QUEUE_LEN))
            self.nl.settimeout(0.5)
            log.info("NFLog monitor started")
        except Exception as e:
            log.warning(f"NFLog start failed: {e}")
            self.nl = None
    
    def register(self, name):
        """注册容器到监控"""
        self.socks[name] = {"rx_count": 0, "last_time": 0}
        log.info(f"[{name}] NFLog monitor registered")
    
    def unregister(self, name):
        if name in self.socks:
            del self.socks[name]
    
    def _nflog_loop(self, name):
        """监听 nflog 消息"""
        buf_size = 4096
        while self.running and name in self.socks:
            try:
                data = self.nl.recv(buf_size)
                # 解析 nflog 消息
                # nflog header: 4 bytes (group, sequence, flags, version)
                # 后面是 nfattr (nla_type | nla_len << 16, data)
                if len(data) < 4:
                    continue
                group_id = struct.unpack('<H', data[2:4])[0]
                if group_id != NFLOG_GROUP:
                    continue
                # 提取 IP 信息（从 nfattr 中）
                # 简化处理：只要收到 nflog 消息就计数
                self.socks[name]["rx_count"] += 1
                self.socks[name]["last_time"] = time.time()
                log.info(f"[{name}] nflog packet received, count={self.socks[name]['rx_count']}")
            except socketlib.timeout:
                continue
            except Exception as e:
                log.warning(f"NFLog loop error: {e}")
    
    def is_active(self, name, threshold=1):
        """检查是否有新连接"""
        if name not in self.socks:
            return False
        info = self.socks[name]
        # 如果 rx_count > 0 且在短时间内，认为活跃
        return info["rx_count"] > 0 and (time.time() - info["last_time"]) < 5


class Monitor:
    def __init__(self):
        self.states = {}
        self.lock = threading.Lock()
        self.nflog = NFLogMonitor()
        self._restore_state()
    
    def _restore_state(self):
        p = Path(STATE_PATH)
        if p.exists():
            try:
                with open(p) as f:
                    saved = json.load(f)
                with self.lock:
                    for name in list(MONITORED.keys()):
                        if name in saved:
                            self.states[name] = saved[name]
            except Exception as e:
                log.warning(f"Restore state failed: {e}")
        # 注册所有监控容器
        for name in MONITORED:
            self.nflog.register(name)
    
    def update_config(self, new_monitored):
        global MONITORED
        MONITORED = new_monitored
        with self.lock:
            for name in new_monitored:
                if name not in self.states:
                    self.states[name] = {}
            for name in list(self.states.keys()):
                if name not in new_monitored:
                    self.nflog.unregister(name)
                    del self.states[name]
    
    def _save_state(self):
        with self.lock:
            with open(STATE_PATH, 'w') as f:
                json.dump(self.states, f, indent=2)
    
    def get_all_status(self):
        result = []
        for name in MONITORED:
            c = get_container(name)
            if not c: continue
            with self.lock:
                s = self.states.get(name, {})
            result.append({"name": name, "status": c["status"],
                           "idle_seconds": s.get("idle_seconds", 0),
                           "nflog_count": self.nflog.socks.get(name, {}).get("rx_count", 0),
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
                    ports = s.get("ports", [])
                    
                    c = get_container(name)
                    if not c: continue
                    
                    # 检查 nflog 是否有活跃连接
                    if monitor.nflog.is_active(name):
                        s["idle_seconds"] = 0
                        # 如果容器 paused，自动唤醒
                        if c["status"] == "paused" and s.get("is_paused_by_us"):
                            log.info(f"[{name}] nflog activity detected, unpausing")
                            ok = unpause_container(name)
                            if ok:
                                s["is_paused"] = False
                                s["is_paused_by_us"] = False
                                monitor._save_state()
                    elif c["status"] == "running":
                        s["idle_seconds"] = s.get("idle_seconds", 0) + CHECK_INTERVAL
                        if s["idle_seconds"] >= idle_timeout:
                            log.info(f"[{name}] idle {s['idle_seconds']}s >= {idle_timeout}s, {sleep_mode}ing")
                            ok = pause_container(name) if sleep_mode == "pause" else stop_container(name)
                            if ok:
                                s["is_paused"] = True
                                s["is_paused_by_us"] = True
                                s["idle_seconds"] = 0
                                monitor._save_state()
                    elif c["status"] == "exited" and s.get("is_paused_by_us"):
                        log.info(f"[{name}] exited, restarting")
                        ok = start_container(name)
                        if ok:
                            s["is_paused"] = False
                            s["is_paused_by_us"] = False
                            s["idle_seconds"] = 0
                            monitor._save_state()
                    elif c["status"] == "paused" and s.get("is_paused_by_us"):
                        # 确认仍空闲
                        pass
        except Exception as e:
            log.warning(f"Monitor error: {e}")


def setup_nflog():
    """设置 iptables nflog 规则，捕获入站连接"""
    rules = []
    for name, cfg in MONITORED.items():
        for port_info in cfg.get("ports", []):
            port = port_info.get("port")
            proto = port_info.get("proto", "tcp")
            # 在 PREROUTING 链记录入站流量到 nflog
            rule = f"-t raw -A PREROUTING -p {proto} --dport {port} -j NFLOG --nflog-group {NFLOG_GROUP}"
            rules.append(rule)
    return rules


def apply_nflog_rules():
    """应用 iptables 规则"""
    rules = setup_nflog()
    for rule in rules:
        try:
            subprocess.run(["iptables", "-C"] + rule.split()[1:], check=False)
            subprocess.run(["iptables"] + rule.split()[1:], check=True)
            log.info(f"Applied: {rule}")
        except subprocess.CalledProcessError as e:
            log.warning(f"Failed to apply rule: {rule}, error: {e}")


# Flask API
from flask import Flask, jsonify, request
app = Flask(__name__)
auth_required = True


def require_auth(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        pw = request.headers.get("X-Auth-Token", ADMIN_PASSWORD)
        if not auth_required or pw == ADMIN_PASSWORD:
            return f(*args, **kwargs)
        return jsonify({"success": False, "error": "unauthorized"}), 401
    return decorated


@app.route("/api/status", methods=["GET"])
@require_auth
def api_status():
    return jsonify(monitor.get_all_status())


@app.route("/api/pause/<name>", methods=["POST"])
@require_auth
def api_pause(name):
    c = get_container(name)
    if not c: return jsonify({"success": False, "error": "容器不存在"}), 404
    if c["status"] == "running": ok = pause_container(name)
    elif c["status"] == "paused": ok = True
    else: ok = False
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
if __name__ == "__main__":
    log.info("Starting NFLog monitor...")
    
    # 应用 iptables 规则
    apply_nflog_rules()
    
    # 启动监控线程
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()
    log.info(f"Docker Pause Manager v4 (NFLog) started on {LISTEN_HOST}:{LISTEN_PORT}")
    
    app.run(host=LISTEN_HOST, port=LISTEN_PORT, debug=False)
