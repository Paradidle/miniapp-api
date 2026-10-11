# -*- coding: utf-8 -*-
"""统一响应格式，与小程序前端约定一致：{ code, message, data }，code=0 成功。"""
from flask import jsonify


def ok(data=None, message='ok'):
    return jsonify({'code': 0, 'message': message, 'data': data if data is not None else {}})


def fail(message='操作失败', code=1, http_status=200):
    # 业务失败统一 HTTP 200，前端只看 code；登录失效用专用 code 便于拦截
    return jsonify({'code': code, 'message': message, 'data': {}}), http_status
