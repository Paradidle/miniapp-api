# -*- coding: utf-8 -*-
"""gunicorn 入口（AGENTS: 供 start.sh / Jenkins 使用）

本地: python wsgi.py
生产: gunicorn -c gunicorn.conf.py wsgi:app
"""
import os

from app.main import app

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', '9201')))
