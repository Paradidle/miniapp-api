# -*- coding: utf-8 -*-
"""gunicorn / 本地运行入口。

本地：python wsgi.py（默认 80 端口，可用 PORT 环境变量修改）
云托管：gunicorn -c gunicorn.conf.py wsgi:app
"""
import os

from app import create_app

app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '80')))
