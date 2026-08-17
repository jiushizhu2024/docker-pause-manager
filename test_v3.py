#!/usr/bin/env python3
"""测试 unbound AF_PACKET SOCK_DGRAM 是否真的能收到 6880 包"""
import socket, struct, time, threading

received = []
stop = False

def listen():
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(0x0800))
    # 不绑定！
    sock.settimeout(0.5)
    global stop
    while not stop:
        try:
            data, addr = sock.recvfrom(65535)
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
                received.append(f"iface={addr[0]} proto={proto} src={src_p} dst={dst_p}")
        except socket.timeout:
            continue
        except Exception as e:
            print(f"ERR: {e}")
    sock.close()

t = threading.Thread(target=listen, daemon=True)
t.start()
print("Listener started")
time.sleep(1)

# 发送连接
print("Sending connection...")
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
print(f"Packets: {len(received)}")
for p in received:
    print(f"  {p}")