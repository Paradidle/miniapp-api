# -*- coding: utf-8 -*-
"""gunicorn 配置：单 worker（全市场矩阵常驻内存，多 worker 会成倍占用）。

内存估算：3207 只 × 3000 天 × 6 字段 × 4 字节 ≈ 1.4 GB，单 worker。
"""
import os

_port = os.environ.get('PORT', '9201')
bind = os.environ.get('GUNICORN_BIND') or f'0.0.0.0:{_port}'

workers = int(os.environ.get('GUNICORN_WORKERS', '1'))
worker_class = 'uvicorn.workers.UvicornWorker'

# 首次加载全市场矩阵可能十几秒，超时给足
timeout = int(os.environ.get('GUNICORN_TIMEOUT', '180'))
graceful_timeout = 30
keepalive = 5
loglevel = 'info'
accesslog = None
