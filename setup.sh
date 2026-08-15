#!/bin/bash
# Docker Pause Manager - 初始化配置文件
# 在首次启动容器前运行此脚本
# 用法: bash setup.sh

DIR="$(cd "$(dirname "$0")" && pwd)"

# 创建 config.json（如果不存在）
if [ ! -f "$DIR/config.json" ]; then
    cat > "$DIR/config.json" << 'EOF'
{
  "admin_password": "admin123",
  "idle_timeout": 300,
  "check_interval": 10,
  "containers": {}
}
EOF
    echo "已创建 $DIR/config.json"
fi

# 创建 state.json（如果不存在）
if [ ! -f "$DIR/state.json" ]; then
    echo '{}' > "$DIR/state.json"
    echo "已创建 $DIR/state.json"
fi

# 确保 docker-compose.yml 包含文件挂载
if [ -f "$DIR/docker-compose.yml" ]; then
    if ! grep -q "config.json:/app/config.json" "$DIR/docker-compose.yml"; then
        echo "警告: docker-compose.yml 未包含 config.json 挂载，请手动添加"
    fi
fi

echo "初始化完成"