#!/usr/bin/env python3
"""不绑定接口，测试能否收到 6880 端口流量"""
import socket, struct, time, threading

received = []
stop = False

def listen():
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(0x0800))
    # 不绑定
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
                if len(tcp_h) < 4: continue
                src_p = struct.unpack('>H', tcp_h[0:2])[0]
                dst_p = struct.unpack('>H', tcp_h[2:4])[0]
            elif proto == 17:
                udp_h = data[ihl:ihl+8]
                if len(udp_h) < 4: continue
                src_p = struct.unpack('>H', udp_h[0:2])[0]
                dst_p = struct.unpack('>H', udp_h[2:4])[0]
            else: continue
            if dst_p == 6880 or src_p == 6880:
                received.append(f"iface={addr[0]} proto={proto} src={src_p} dst={dst_p}")
        except socket.timeout:
            continue
        except Exception as e:
            print(f"err: {e}")
    sock.close()

t = threading.Thread(target=listen, daemon=True)
t.start()
print("Listener started (unbound)")
time.sleep(1)

s = socket.socket()
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