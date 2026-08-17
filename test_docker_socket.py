import socket, json, os, threading, time
from pathlib import Path

DOCKER_SOCKET = "/var/run/docker.sock"


def docker_request(method, path, data=None):
    """通过 unix socket 调用 Docker API"""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(DOCKER_SOCKET)
        
        # 构建 HTTP 请求
        body = json.dumps(data).encode() if data else b""
        request = f"{method} {path} HTTP/1.1\r\n"
        request += f"Content-Type: application/json\r\n"
        request += f"Content-Length: {len(body)}\r\n"
        request += f"Host: localhost\r\n"
        request += f"Connection: close\r\n"
        request += "\r\n"
        request += body.decode()
        
        sock.sendall(request.encode())
        
        # 接收响应
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
            if b"\r\n\r\n" in response:
                header_end = response.find(b"\r\n\r\n")
                # 解析 Content-Length
                headers = response[:header_end].decode()
                cl_line = [l for l in headers.split("\r\n") if l.lower().startswith("content-length:")]
                content_length = int(cl_line[0].split(": ")[1]) if cl_line else -1
                
                body = response[header_end + 4:]
                if content_length > 0:
                    while len(body) < content_length:
                        chunk = sock.recv(4096)
                        if not chunk:
                            break
                        body += chunk
                
                if body:
                    return json.loads(body)
                return None
    except Exception as e:
        print(f"Docker socket error: {e}")
        return None
    finally:
        sock.close()


# 测试
if __name__ == "__main__":
    try:
        result = docker_request("GET", "/containers/json")
        print(f"Success: {len(result) if result else 0} containers")
        if result:
            for c in result[:3]:
                print(f"  - {c['Names'][0]}: {c['State']['Status']}")
    except Exception as e:
        print(f"Error: {e}")