# -*- coding: utf-8 -*-
"""Flask 应用工厂。"""
import logging

from flask import Flask

from .config import Config
from .response import fail, ok
from .routes import register_routes


class _PathPrefixMiddleware:
    """剥离 nginx 反代路径前缀（如 /prosperity-bookkeeping-api）。

    nginx 的 proxy_pass 末尾不带 / 时，/prosperity-bookkeeping-api/api/xxx
    会原样透传到 Flask，这里统一剥掉前缀再交给路由；不带前缀的直连请求不受影响。
    """

    def __init__(self, wsgi_app, prefix):
        self.wsgi_app = wsgi_app
        self.prefix = (prefix or '').rstrip('/')

    def __call__(self, environ, start_response):
        path = environ.get('PATH_INFO', '')
        if self.prefix and path != self.prefix and path.startswith(self.prefix + '/'):
            environ['PATH_INFO'] = path[len(self.prefix):]
        return self.wsgi_app(environ, start_response)


def create_app():
    app = Flask(__name__)
    app.json.ensure_ascii = False  # 中文直接输出，不转义成 \uXXXX
    app.wsgi_app = _PathPrefixMiddleware(app.wsgi_app, Config.URL_PREFIX)

    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')

    register_routes(app)

    @app.route('/')
    def index():
        return ok({'service': 'prosperity-bookkeeping-api'})

    @app.route('/healthz')
    def healthz():
        """云托管健康检查：顺带探测数据库连通性。"""
        try:
            from .db import pool
            conn = pool.connection()
            cur = conn.cursor()
            cur.execute('SELECT 1')
            cur.close()
            conn.close()
            return ok({'db': 'ok'})
        except Exception as e:
            app.logger.exception('health check failed')
            return fail('数据库不可用: %s' % e, code=503, http_status=503)

    @app.errorhandler(404)
    def not_found(_e):
        return fail('接口不存在', code=404, http_status=404)

    @app.errorhandler(Exception)
    def server_error(e):
        app.logger.exception('unhandled error: %s', e)
        return fail('服务器开小差了，请稍后再试', code=500, http_status=500)

    return app
