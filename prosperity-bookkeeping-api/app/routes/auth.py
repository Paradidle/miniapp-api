# -*- coding: utf-8 -*-
"""登录与用户资料接口。"""
from flask import Blueprint, g, request

from ..auth import issue_token, login_required
from ..db import db_cursor, fetchone
from ..response import fail, ok
from ..wechat import WeChatError, code2session

bp = Blueprint('auth', __name__)


def _extract_profile(body):
    """兼容 {nickName, avatarUrl} 与包一层 {userInfo: {...}} 两种传参。"""
    info = body.get('userInfo') or body
    nickname = str(info.get('nickName') or '').strip()[:64]
    avatar_url = str(info.get('avatarUrl') or '').strip()[:512]
    return nickname, avatar_url


def _upsert_user(openid, union_id=None, nickname='', avatar_url=''):
    """按 openid 插入或更新用户；空昵称/头像不覆盖已有值。"""
    with db_cursor(commit=True) as cur:
        cur.execute(
            '''INSERT INTO users (openid, union_id, nickname, avatar_url)
               VALUES (%s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE
                 union_id   = COALESCE(VALUES(union_id), union_id),
                 nickname   = IF(VALUES(nickname) = '', nickname, VALUES(nickname)),
                 avatar_url = IF(VALUES(avatar_url) = '', avatar_url, VALUES(avatar_url))''',
            (openid, union_id, nickname, avatar_url)
        )
    return fetchone('SELECT * FROM users WHERE openid = %s', (openid,))


def _user_info(user):
    return {'nickName': user['nickname'], 'avatarUrl': user['avatar_url']}


@bp.route('/api/login', methods=['POST'])
def login():
    """登录：优先取云托管网关注入的 openid，否则用 wx.login 的 code 换取。"""
    body = request.get_json(silent=True) or {}
    nickname, avatar_url = _extract_profile(body)

    # 1) 云托管内部调用（wx.cloud.callContainer）
    openid = request.headers.get('X-WX-OPENID', '').strip()
    union_id = request.headers.get('X-WX-UNIONID', '').strip() or None

    # 2) code 换 openid
    if not openid:
        code = str(body.get('code') or '').strip()
        if not code:
            return fail('缺少登录 code', code=401)
        try:
            session = code2session(code)
        except WeChatError as e:
            return fail(str(e), code=401)
        openid = session['openid']
        union_id = session.get('unionid')

    user = _upsert_user(openid, union_id, nickname, avatar_url)
    return ok({
        'token': issue_token(user['id'], user['openid']),
        'openid': user['openid'],
        'userInfo': _user_info(user),
    })


@bp.route('/api/user/update', methods=['POST'])
@login_required
def update_user():
    """更新当前用户全局资料（对应前端 saveUserInfo 的云端同步）。"""
    body = request.get_json(silent=True) or {}
    nickname, avatar_url = _extract_profile(body)
    if not nickname:
        return fail('昵称不能为空')

    with db_cursor(commit=True) as cur:
        cur.execute(
            'UPDATE users SET nickname=%s, avatar_url=%s WHERE id=%s',
            (nickname, avatar_url, g.user['id'])
        )
    return ok({'userInfo': {'nickName': nickname, 'avatarUrl': avatar_url}})


@bp.route('/api/room/member/update', methods=['POST'])
@login_required
def update_member_info():
    """修改当前用户在某个房间内的昵称/头像（同时同步全局资料）。"""
    body = request.get_json(silent=True) or {}
    room_id = body.get('roomId')
    nickname, avatar_url = _extract_profile(body)
    if not room_id:
        return fail('缺少 roomId')
    if not nickname:
        return fail('昵称不能为空')

    member = fetchone(
        'SELECT id FROM room_members WHERE room_id=%s AND user_id=%s',
        (room_id, g.user['id'])
    )
    if not member:
        return fail('你不在该房间中')

    with db_cursor(commit=True) as cur:
        cur.execute(
            '''UPDATE room_members SET nickname=%s, avatar_url=%s
               WHERE room_id=%s AND user_id=%s''',
            (nickname, avatar_url, room_id, g.user['id'])
        )
        cur.execute(
            'UPDATE users SET nickname=%s, avatar_url=%s WHERE id=%s',
            (nickname, avatar_url, g.user['id'])
        )
    return ok({'userInfo': {'nickName': nickname, 'avatarUrl': avatar_url}})
