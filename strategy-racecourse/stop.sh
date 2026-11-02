#!/bin/bash
# 停止 strategy-racecourse（先优雅退出，超时再强制结束）
# 用法：./stop.sh

set -u

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$APP_DIR/gunicorn.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "服务未运行（找不到 $PID_FILE）"
    exit 0
fi

pid="$(cat "$PID_FILE")"
if ! kill -0 "$pid" 2>/dev/null; then
    echo "进程 $pid 已不存在，清理 PID 文件"
    rm -f "$PID_FILE"
    exit 0
fi

kill -TERM "$pid"
echo "正在停止服务，PID=$pid ..."

for _ in $(seq 1 15); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
done

if kill -0 "$pid" 2>/dev/null; then
    echo "优雅退出超时，强制结束进程"
    kill -KILL "$pid" 2>/dev/null || true
    sleep 1
fi

rm -f "$PID_FILE"
echo "服务已停止"
