#!/usr/bin/env python3
"""在 docker-pause-manager 容器内测试 AF_PACKET SOCK_DGRAM 是否能收到 6880 端口的包"""
import socket, struct, time, threading

received = []
stop = False

def listen():
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(0x0800))
    try:
        sock.bind(("", socket.htons(0x0800)))
    except OSError as e:
        print(f"bind error: {e}")
    sock.settimeout(0.5)
    global stop
    while not stop:
        try:
            data = sock.recvfrom(65535)[0]
            if len(data) < 20: continue
            ip_header = data[0:20]
            ihl = (ip_header[0] & 0xF) * 4
            if len(data) < ihl + 8: continue
            proto = ip_header[9]
            if proto == 6:
                tcp_h = data[ihl:ihl+20]
                dst_port = struct.unpack('>H', tcp_h[2:4])[0]
                src_port = struct.unpack('>H', tcp_h[0:2])[0]
            elif proto == 17:
                udp_h = data[ihl:ihl+8]
                dst_port = struct.unpack('>H', udp_h[2:4])[0]
                src_port = struct.unpack('>H', udp_h[0:2])[0]
            else:
                continue
            if dst_port == 6880 or src_port == 6880:
                received.append(f"proto={proto} src={src_port} dst={dst_port} len={len(data)}")
        except socket.timeout:
            continue
    sock.close()

t = threading.Thread(target=listen, daemon=True)
t.start()
print("Listener started, sending connection in 1s...")
time.sleep(1)

# 发送连接
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(3)
try:
    s.connect(('192.168.10.5', 6880))
    print("Connected!")
except Exception as e:
    print(f"Connect: {e}")
s.close()

time.sleep(3)
stop = True
print(f"Packets received on port 6880: {len(received)}")
for p in received[:10]:
    print(f"  {p}")