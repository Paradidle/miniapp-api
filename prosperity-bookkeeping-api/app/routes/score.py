# -*- coding: utf-8 -*-
"""积分转账与流水查询。"""
from flask import Blueprint, g, request

from ..auth import login_required
from ..db import db_cursor, fetchall, fetchone
from ..response import fail, ok
from .room import dt_ms, dt_text

bp = Blueprint('score', __name__)


@bp.route('/api/score/transfer', methods=['POST'])
@login_required
def transfer_score():
    """当前用户向 toOpenid 转账 amount：付款人 -amount，收款人 +amount。

    两步更新 + 写流水在同一事务内完成，保证总分恒为 0。
    """
    body = request.get_json(silent=True) or {}
    room_id = body.get('roomId')
    to_openid = str(body.get('toOpenid') or '').strip()
    try:
        amount = int(body.get('amount', 0))
    except (TypeError, ValueError):
        return fail('金额格式错误')

    if not room_id:
        return fail('缺少 roomId')
    if not to_openid:
        return fail('缺少收款人')
    if amount <= 0:
        return fail('金额必须大于 0')

    room = fetchone('SELECT id, status FROM rooms WHERE id=%s', (room_id,))
    if not room:
        return fail('房间不存在')
    if room['status'] != 1:
        return fail('游戏未开始，无法记分')

    payer = fetchone(
        'SELECT id, openid FROM room_members WHERE room_id=%s AND user_id=%s',
        (room_id, g.user['id'])
    )
    receiver = fetchone(
        'SELECT id, openid FROM room_members WHERE room_id=%s AND openid=%s',
        (room_id, to_openid)
    )
    if not payer:
        return fail('你不在该房间中')
    if not receiver:
        return fail('收款人不在该房间中')
    if payer['id'] == receiver['id']:
        return fail('不能给自己转账')

    with db_cursor(commit=True) as cur:
        cur.execute(
            'UPDATE room_members SET score = score - %s WHERE id=%s',
            (amount, payer['id'])
        )
        cur.execute(
            'UPDATE room_members SET score = score + %s WHERE id=%s',
            (amount, receiver['id'])
        )
        cur.execute(
            '''INSERT INTO score_records
                 (room_id, from_member_id, to_member_id, from_openid, to_openid,
                  amount, operator_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)''',
            (room_id, payer['id'], receiver['id'],
             payer['openid'], receiver['openid'], amount, g.user['id'])
        )
        record_id = cur.lastrowid

    return ok({'recordId': str(record_id)})


@bp.route('/api/score/records', methods=['GET'])
@login_required
def get_records():
    """房间流水（倒序，最多 200 条），昵称取成员当前资料。"""
    room_id = request.args.get('roomId')
    if not room_id:
        return fail('缺少 roomId')

    member = fetchone(
        'SELECT id FROM room_members WHERE room_id=%s AND user_id=%s',
        (room_id, g.user['id'])
    )
    if not member:
        return fail('你不在该房间中')

    rows = fetchall(
        '''SELECT sr.id, sr.amount, sr.remark, sr.created_at,
                  sr.from_openid, sr.to_openid,
                  fm.nickname AS from_nick_name, fm.avatar_url AS from_avatar_url,
                  tm.nickname AS to_nick_name,   tm.avatar_url AS to_avatar_url
           FROM score_records sr
           JOIN room_members fm ON fm.id = sr.from_member_id
           JOIN room_members tm ON tm.id = sr.to_member_id
           WHERE sr.room_id = %s
           ORDER BY sr.id DESC
           LIMIT 200''',
        (room_id,)
    )

    records = [{
        'id': str(r['id']),
        'fromOpenid': r['from_openid'],
        'toOpenid': r['to_openid'],
        'fromNickName': r['from_nick_name'],
        'toNickName': r['to_nick_name'],
        'fromAvatarUrl': r['from_avatar_url'],
        'toAvatarUrl': r['to_avatar_url'],
        'amount': r['amount'],
        'remark': r['remark'] or '',
        'createdAt': dt_text(r['created_at']),
        'createTime': dt_ms(r['created_at']),
    } for r in rows]

    return ok({'records': records})
