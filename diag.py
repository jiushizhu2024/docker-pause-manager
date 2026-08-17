#!/usr/bin/env python3
"""诊断 unbound AF_PACKET socket 是否能在 app.py 运行时捕获 6880 流量"""
import socket, struct, time, subprocess, threading

received = []
stop = False

def listen():
    sock = socket.AF_PACKET or socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(0x0800))
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(0x0800))
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
                received.append(f"iface={addr[0]} proto={proto} src={src_p} dst={dst_p} len={len(data)}")
        except socket.timeout:
            continue
        except Exception as e:
            print(f"RX error: {e}")
    sock.close()

t = threading.Thread(target=listen, daemon=True)
t.start()
time.sleep(1)
print("--- Sending connection ---")
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
print(f"\nPackets: {len(received)}")
for p in received[:10]:
    print(f"  {p}")
r = subprocess.run(['docker', 'inspect', '-f', '{{.State.Status}}', 'ariang'], capture_output=True, text=True)
print(f"ariang: {r.stdout.strip()}")