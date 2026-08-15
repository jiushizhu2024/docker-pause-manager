#!/usr/bin/env python3
"""
Docker Pause Manager - Web UI
自动 pause/unpause Docker 容器，基于 conntrack NEW 连接检测唤醒，空闲超时 pause。
支持多端口容器：一个容器可监控多个 TCP/UDP 端口，任一端口有连接即唤醒，全部端口空闲才 pause。
支持管理员登录认证、自动端口选择。
"""
import json, os, time, threading, subprocess, logging, hashlib, secrets
from functools import wraps
from flask import Flask, request, jsonify, send_file
import docker

# 使用 Unix socket 直接连接 Docker daemon
DOCKER_SOCKET = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
cli = docker.DockerClient(base_url=f"unix://{DOCKER_SOCKET}")

try:
    import jwt
except ImportError:
    # 如果没有安装 jwt，动态生成一个简单的 token
    jwt = None

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/app/config.json")
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "5287"))
JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32))
JWT_ALGORITHM = "HS256"
JWT_EXP_HOURS = 24

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pause-manager")

app = Flask(__name__, static_folder=None)
