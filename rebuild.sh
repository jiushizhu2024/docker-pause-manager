#!/bin/bash
set -e
cd /volume1/docker/docker-pause-manager
echo "== [1/4] 停止并移除旧容器 =="
docker stop docker-pause-manager 2>/dev/null || true
docker rm docker-pause-manager 2>/dev/null || true
echo "== [2/4] 用新镜像重建 =="
docker compose up -d
sleep 6
echo "== [3/4] 容器状态 =="
docker ps --filter name=docker-pause-manager --format "image={{.Image}} status={{.Status}}"
echo "== [4/4] 新代码是否生效（应输出 3 或更多）=="
docker exec docker-pause-manager grep -c "_add_iptables_drop_rules" /app/app.py
echo "== [5/5] 清理可能残留的 iptables DROP 规则（重建瞬间）=="
iptables -L INPUT -n | grep -c "DROP" || true
echo DONE
