# -*- coding: utf-8 -*-
"""gunicorn 配置：云托管低内存方案，1 worker + 线程并发。

内存占用约 40-70MB；实例规格上来后可调 workers，但每多一个 worker
约多占 25-35MB。
"""
import os

# bind：可用 GUNICORN_BIND 精确指定（自有服务器走 nginx 反代时为 127.0.0.1:8089）；
# 未指定时回落到 0.0.0.0:PORT，默认 80（云托管/Docker 场景）
bind = os.environ.get('GUNICORN_BIND') or ('0.0.0.0:%s' % os.environ.get('PORT', '80'))
workers = int(os.environ.get('GUNICORN_WORKERS', '1'))
threads = int(os.environ.get('GUNICORN_THREADS', '4'))
worker_class = 'gthread'
timeout = 30
graceful_timeout = 20
keepalive = 5
loglevel = 'info'
