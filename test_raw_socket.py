import socket

socket_path = "/var/run/docker.sock"

try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(socket_path)
    request = b"GET /containers/json HTTP/1.1\r\nHost: localhost\r\n\r\n"
    s.sendall(request)
    response = b""
    s.settimeout(5)
    while True:
        try:
            data = s.recv(4096)
            if not data:
                break
            response += data
        except socket.timeout:
            break
    s.close()
    print("Direct socket connection OK")
    print(response.decode('utf-8', errors='ignore')[:500])
except Exception as e:
    print("Direct socket FAILED:", e)
