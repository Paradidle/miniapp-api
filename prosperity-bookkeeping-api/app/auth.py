# -*- coding: utf-8 -*-
"""登录态：双轨制。

1) 微信云托管调用：wx.cloud.callContainer 经网关转发时会注入 X-WX-OPENID
   请求头（外部无法伪造），直接信任，免 token；
2) 公网/本地调试：/api/login 换发签名 token，前端放 Authorization: Bearer。
"""
from functools import wraps

from flask import g, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import Config
from .db import fetchone
from .response import fail

_serializer = URLSafeTimedSerializer(Config.TOKEN_SECRET, salt='prosperity-login')


def issue_token(user_id, openid):
    return _serializer.dumps({'uid': user_id, 'openid': openid})


def _load_user_by_openid(openid):
    return fetchone('SELECT * FROM users WHERE openid = %s', (openid,))


def resolve_current_user():
    """返回当前用户 dict，无法识别返回 None。"""
    # 1) 云托管网关注入的 openid
    wx_openid = request.headers.get('X-WX-OPENID', '').strip()
    if wx_openid:
        return _load_user_by_openid(wx_openid)

    # 2) Bearer token
    auth = request.headers.get('Authorization', '').strip()
    if auth.startswith('Bearer '):
        token = auth[7:].strip()
        try:
            payload = _serializer.loads(token, max_age=Config.TOKEN_EXPIRE_SECONDS)
        except (BadSignature, SignatureExpired):
            return None
        return fetchone('SELECT * FROM users WHERE id = %s', (payload.get('uid'),))

    return None


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = resolve_current_user()
        if not user:
            return fail('请先登录', code=401)
        g.user = user
        return fn(*args, **kwargs)
    return wrapper


def current_user():
    """视图内取当前用户（需配合 login_required）。"""
    return g.user
