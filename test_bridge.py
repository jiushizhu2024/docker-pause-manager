#!/usr/bin/env python3
"""测试绑定到 br-8955f0b241e0 接口能否抓到 6880 端口流量"""
import socket, struct, time, threading

received = []
stop = False
IFACE = "br-8955f0b241e0"

def listen():
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(0x0800))
    try:
        sock.bind((IFACE, socket.htons(0x0800)))
        print(f"bound to {IFACE}")
    except OSError as e:
        print(f"bind error: {e}")
    sock.settimeout(0.5)
    global stop
    while not stop:
        try:
            data = sock.recvfrom(65535)[0]
            if len(data) < 20: continue
            ihl = (data[0] & 0xF) * 4
            if len(data) < ihl + 8: continue
            proto = data[9]
            if proto == 6:
                tcp_h = data[ihl:ihl+20]
                src_p = struct.unpack('>H', tcp_h[0:2])[0]
                dst_p = struct.unpack('>H', tcp_h[2:4])[0]
            elif proto == 17:
                udp_h = data[ihl:ihl+8]
                src_p = struct.unpack('>H', udp_h[0:2])[0]
                dst_p = struct.unpack('>H', udp_h[2:4])[0]
            else: continue
            if dst_p == 6880 or src_p == 6880:
                received.append(f"proto={proto} src={src_p} dst={dst_p} len={len(data)}")
        except socket.timeout:
            continue
    sock.close()

t = threading.Thread(target=listen, daemon=True)
t.start()
print("Listener started, sending connection in 1s...")
time.sleep(1)

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
print(f"\nPackets on port 6880: {len(received)}")
for p in received[:10]:
    print(f"  {p}")