#!/bin/bash
# 启动 prosperity-bookkeeping-api（gunicorn 后台运行，日志写入 info.log）
# 用法：./start.sh

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$APP_DIR/gunicorn.pid"
LOG_FILE="$APP_DIR/info.log"

# gunicorn 监听地址：默认只监听本机 8089，由 nginx 反向代理对外的 80 端口。
# 若不经过 nginx 直接暴露服务，可临时执行：GUNICORN_BIND=0.0.0.0:80 ./start.sh
GUNICORN_BIND="${GUNICORN_BIND:-127.0.0.1:8089}"
export GUNICORN_BIND
# 从 bind 中提取端口，仅供下面占用检测使用
PORT="${GUNICORN_BIND##*:}"

# 已在运行则不重复启动
if [ -f "$PID_FILE" ]; then
    pid="$(cat "$PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
        echo "服务已在运行，PID=$pid"
        exit 1
    else
        # 残留的过期 PID 文件
        rm -f "$PID_FILE"
    fi
fi

# 启动前检查端口是否已被其他进程占用（多半是旧 gunicorn 或 nginx）
if ss -lnt 2>/dev/null | grep -qE "[:.]${PORT}[[:space:]]"; then
    echo "端口 ${PORT} 已被占用，无法启动。占用进程："
    ss -lntp 2>/dev/null | grep -E "[:.]${PORT}[[:space:]]"
    echo "若确认是旧的本服务进程，可执行：pkill -f 'gunicorn.*wsgi:app'"
    exit 1
fi

cd "$APP_DIR" || exit 1

# --daemon 后台运行；访问日志和错误日志（含应用日志）统一写入 info.log
.venv/bin/gunicorn -c gunicorn.conf.py wsgi:app \
    --daemon \
    --pid "$PID_FILE" \
    --access-logfile "$LOG_FILE" \
    --error-logfile "$LOG_FILE"

# 等待并确认进程存活
sleep 1
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "启动成功，PID=$(cat "$PID_FILE")，监听 $GUNICORN_BIND（由 nginx 代理 80 端口）"
    echo "日志文件：$LOG_FILE（tail -f $LOG_FILE）"
else
    echo "启动失败，请查看日志：$LOG_FILE"
    exit 1
fi
