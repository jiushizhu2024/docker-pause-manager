#!/usr/bin/env python3
"""在容器内直接测试 AF_PACKET 能否收到 6880 流量"""
import socket, struct, time

sock = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(0x0800))
# 不绑定
sock.settimeout(0.5)
print("Socket created, starting recv...")

def test_recv():
    global got
    try:
        data, addr = sock.recvfrom(65535)
        if len(data) < 20: return
        ihl = (data[0] & 0xF) * 4
        if len(data) < ihl + 8: return
        proto = data[9]
        if proto == 6:
            tcp_h = data[ihl:ihl+20]
            src_p = struct.unpack('>H', tcp_h[0:2])[0]
            dst_p = struct.unpack('>H', tcp_h[2:4])[0]
        elif proto == 17:
            udp_h = data[ihl:ihl+8]
            src_p = struct.unpack('>H', udp_h[0:2])[0]
            dst_p = struct.unpack('>H', udp_h[2:4])[0]
        else: return
        if dst_p == 6880 or src_p == 6880:
            got.append(f"src={src_p} dst={dst_p} iface={addr[0]}")
    except socket.timeout:
        pass

got = []
start = time.time()
while time.time() - start < 3:
    test_recv()

print(f"Packets in 3s: {len(got)}")
for g in got:
    print(f"  {g}")

# 现在发连接
print("\nSending connection...")
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(3)
try:
    s.connect(('192.168.10.5', 6880))
    print("Connected!")
except Exception as e:
    print(f"Connect: {e}")
s.close()

time.sleep(2)
print(f"\nAfter connection: {len(got)} packets")
for g in got:
    print(f"  {g}")

sock.close()