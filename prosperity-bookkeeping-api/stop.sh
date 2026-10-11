#!/bin/bash
# 停止 prosperity-bookkeeping-api（先优雅退出，超时再强制结束）
# 用法：./stop.sh

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

# TERM：gunicorn 优雅退出（处理完在途请求后退出）
kill -TERM "$pid"
echo "正在停止服务，PID=$pid ..."

# 最多等待 10 秒
for _ in $(seq 1 10); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
done

# 仍未退出则强制 KILL
if kill -0 "$pid" 2>/dev/null; then
    echo "优雅退出超时，强制结束进程"
    kill -KILL "$pid"
    sleep 1
fi

rm -f "$PID_FILE"
echo "服务已停止"
