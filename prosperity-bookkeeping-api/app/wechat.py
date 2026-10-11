# -*- coding: utf-8 -*-
"""微信服务端 API 封装：code2session 与小程序码（access_token 内存缓存）。"""
import threading
import time

import requests

from .config import Config

# access_token 有效期 7200 秒，提前 5 分钟刷新
_TOKEN_CACHE = {'token': '', 'expire_at': 0}
_TOKEN_LOCK = threading.Lock()


class WeChatError(Exception):
    pass


def code2session(code):
    """用 wx.login 的 code 换 openid。

    返回: {'openid': ..., 'unionid': ..., 'session_key': ...}
    """
    if not Config.WX_APPID or not Config.WX_SECRET:
        raise WeChatError('服务端未配置 WX_APPID/WX_SECRET')

    url = 'https://api.weixin.qq.com/sns/jscode2session'
    resp = requests.get(url, params={
        'appid': Config.WX_APPID,
        'secret': Config.WX_SECRET,
        'js_code': code,
        'grant_type': 'authorization_code',
    }, timeout=8)
    data = resp.json()
    if data.get('errcode'):
        raise WeChatError('微信登录失败：%s' % data.get('errmsg', data.get('errcode')))
    if 'openid' not in data:
        raise WeChatError('微信登录返回异常')
    return data


def get_access_token():
    """获取（并缓存）公众号/小程序接口调用凭据。"""
    now = int(time.time())
    if _TOKEN_CACHE['token'] and now < _TOKEN_CACHE['expire_at']:
        return _TOKEN_CACHE['token']

    with _TOKEN_LOCK:
        # 双检，避免多线程同时刷新
        now = int(time.time())
        if _TOKEN_CACHE['token'] and now < _TOKEN_CACHE['expire_at']:
            return _TOKEN_CACHE['token']

        if not Config.WX_APPID or not Config.WX_SECRET:
            raise WeChatError('服务端未配置 WX_APPID/WX_SECRET')

        resp = requests.get('https://api.weixin.qq.com/cgi-bin/token', params={
            'grant_type': 'client_credential',
            'appid': Config.WX_APPID,
            'secret': Config.WX_SECRET,
        }, timeout=8)
        data = resp.json()
        if data.get('errcode'):
            raise WeChatError('获取 access_token 失败：%s' % data.get('errmsg'))
        _TOKEN_CACHE['token'] = data['access_token']
        _TOKEN_CACHE['expire_at'] = now + min(int(data.get('expires_in', 7200)) - 300, 7200)
        return _TOKEN_CACHE['token']


def get_unlimited_qrcode(room_no):
    """生成加入房间的小程序码，返回图片二进制。

    scene 会透传到启动参数 scene 中，格式 roomNo=xxxxxx
    （小程序端需在 app/页面 onLoad 解析 scene，后续前端改造时接入）。
    """
    token = get_access_token()
    resp = requests.post(
        'https://api.weixin.qq.com/wxa/getwxacodeunlimited',
        params={'access_token': token},
        json={
            'scene': 'roomNo=%s' % room_no,
            'page': 'pages/join/join',
            'envVersion': Config.WX_ENV_VERSION,
            'checkPath': False,   # 体验版/开发版页面可能尚未发布，关闭校验更稳妥
            'width': 280,
        },
        timeout=10,
    )
    content_type = resp.headers.get('Content-Type', '')
    if 'image' not in content_type:
        # 出错时微信返回 JSON
        try:
            err = resp.json()
            raise WeChatError('生成小程序码失败：%s' % err.get('errmsg', err.get('errcode')))
        except ValueError:
            raise WeChatError('生成小程序码失败：未知响应')
    return resp.content
