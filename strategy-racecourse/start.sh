#!/bin/bash
# 启动 strategy-racecourse（gunicorn 后台运行，日志写入 info.log）
# 用法：./start.sh
# 自定义监听：GUNICORN_BIND=127.0.0.1:9201 ./start.sh

set -u

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$APP_DIR/gunicorn.pid"
LOG_FILE="$APP_DIR/info.log"

GUNICORN_BIND="${GUNICORN_BIND:-0.0.0.0:9201}"
export GUNICORN_BIND
PORT="${GUNICORN_BIND##*:}"
export PORT

# 数据目录与存储目录（可在外部覆盖）
export TDX_VIPDOC="${TDX_VIPDOC:-/data/vipdoc}"
export RACE_DATA="${RACE_DATA:-$APP_DIR/data}"

# 数据目录自检（运行时也会自动探测，这里只做提示）
if [ ! -f "$TDX_VIPDOC/sh/lday/sh000001.day" ] && \
   [ ! -f "$TDX_VIPDOC/vipdoc/sh/lday/sh000001.day" ]; then
    echo "[警告] TDX_VIPDOC=$TDX_VIPDOC 下未找到 sh/lday/sh000001.day"
    echo "       服务仍会启动（运行时自动向上/向下探测 vipdoc），但请确认数据路径。"
fi

# 已在运行则不重复启动
if [ -f "$PID_FILE" ]; then
    pid="$(cat "$PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
        echo "服务已在运行，PID=$pid"
        exit 1
    else
        rm -f "$PID_FILE"
    fi
fi

# 端口占用检查
if command -v ss >/dev/null 2>&1 && ss -lnt 2>/dev/null | grep -qE "[:.]${PORT}[[:space:]]"; then
    echo "端口 ${PORT} 已被占用，无法启动。占用进程："
    ss -lntp 2>/dev/null | grep -E "[:.]${PORT}[[:space:]]"
    echo "若确认是旧的本服务进程，可执行：pkill -f 'gunicorn.*wsgi:app'"
    exit 1
fi

cd "$APP_DIR" || exit 1

if [ ! -x ".venv/bin/gunicorn" ]; then
    echo "未找到 .venv/bin/gunicorn，请先安装依赖："
    echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

.venv/bin/gunicorn -c gunicorn.conf.py wsgi:app \
    --daemon \
    --pid "$PID_FILE" \
    --access-logfile "$LOG_FILE" \
    --error-logfile "$LOG_FILE"

# 等待并确认进程存活
sleep 2
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "启动成功，PID=$(cat "$PID_FILE")，监听 ${GUNICORN_BIND}"
    echo "数据目录：$TDX_VIPDOC"
    echo "日志文件：$LOG_FILE（tail -f $LOG_FILE）"
else
    echo "启动失败，请查看日志：$LOG_FILE"
    tail -n 30 "$LOG_FILE" 2>/dev/null
    exit 1
fi
