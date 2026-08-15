import urllib.request
import json
import os

socket_path = "/var/run/docker.sock"
url = f"http://localhost/containers/json"

# Use Unix socket transport
from urllib.request import Request
from urllib.parse import urlencode

# Docker API endpoint
req = Request("http://localhost/containers/json")

# Try using urllib with Unix socket
import http.client
import socket

class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path, timeout=10):
        super().__init__('localhost', timeout=timeout)
        self.socket_path = socket_path
    
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)

http.client.HTTPConnectionPool = http.client.HTTPConnectionPool

# Try direct socket
try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(socket_path)
    request = b"GET /containers/json HTTP/1.1\r\nHost: localhost\r\n\r\n"
    s.sendall(request)
    response = b""
    while True:
        data = s.recv(4096)
        if not data:
            break
        response += data
    s.close()
    print("Direct socket connection OK")
    print(response.decode('utf-8', errors='ignore')[:500])
except Exception as e:
    print("Direct socket FAILED:", e)
