#!/usr/bin/env python3
"""测试 nflog 是否能捕获 6880 端口的入站连接"""
import subprocess, socket as socketlib, struct, time, threading

NFLOG_GROUP = 100

# 1. 设置 iptables 规则
rule = ["iptables", "-t", "raw", "-A", "PREROUTING", "-p", "tcp", "--dport", "6880", "-j", "NFLOG", "--nflog-group", str(NFLOG_GROUP)]
print(f"Applying rule: {' '.join(rule)}")
result = subprocess.run(rule, capture_output=True, text=True)
print(f"iptables result: {result.returncode}, stderr: {result.stderr}")

# 2. 创建 netlink socket
sock = socketlib.socket(socketlib.AF_NETLINK, socketlib.SOCK_RAW, 14)  # NETLINK_NETFILTER
sock.bind((0, 10))  # group 0, queue length 10
sock.settimeout(0.5)
print("Netlink socket bound")

# 3. 监听线程
received = []
stop = False

def listen():
    global stop
    while not stop:
        try:
            data = sock.recv(4096)
            if len(data) < 4: continue
            # 解析 nflog header
            version = data[0]
            msg_type = data[1]
            flags = struct.unpack('<H', data[2:4])[0]
            seq = struct.unpack('<I', data[4:8])[0]
            group_id = struct.unpack('<I', data[8:12])[0]
            print(f"nflog: ver={version} type={msg_type} group={group_id} seq={seq}")
            if group_id == NFLOG_GROUP:
                received.append(f"group={group_id} seq={seq} len={len(data)}")
        except socketlib.timeout:
            continue

t = threading.Thread(target=listen, daemon=True)
t.start()
print("Listening for nflog messages...")

# 4. 发送测试连接
time.sleep(1)
print("\nSending connection...")
s = socketlib.socket(socketlib.AF_INET, socketlib.SOCK_STREAM)
s.settimeout(3)
try:
    s.connect(('192.168.10.5', 6880))
    print("Connected!")
except Exception as e:
    print(f"Connect: {e}")
s.close()

# 5. 等待结果
time.sleep(3)
stop = True
print(f"\nReceived nflog messages: {len(received)}")
for r in received:
    print(f"  {r}")

# 清理
subprocess.run(["iptables", "-t", "raw", "-D", "PREROUTING", "-p", "tcp", "--dport", "6880", "-j", "NFLOG", "--nflog-group", str(NFLOG_GROUP)], capture_output=True)
print("Rule removed")
sock.close()