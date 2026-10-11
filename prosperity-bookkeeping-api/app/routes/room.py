# -*- coding: utf-8 -*-
"""房间相关接口：创建、加入、开始、结束、详情、我的房间。"""
import random

from flask import Blueprint, g, request

from ..auth import login_required
from ..db import db_cursor, fetchall, fetchone
from ..response import fail, ok
from .auth import _extract_profile, _upsert_user

bp = Blueprint('room', __name__)

# 房间状态：数据库 TINYINT <-> 前端字符串枚举
STATUS_TEXT = {0: 'waiting', 1: 'playing', 2: 'finished'}
STATUS_CODE = {v: k for k, v in STATUS_TEXT.items()}


def dt_ms(dt):
    """DATETIME -> 毫秒时间戳。

    MySQL 的 CURRENT_TIMESTAMP 按会话时区（容器 TZ=Asia/Shanghai）存，
    这里用本地时区解释，与库内时间保持一致。
    """
    import time
    from datetime import datetime
    if isinstance(dt, datetime):
        return int(time.mktime(dt.timetuple()) * 1000)
    return 0


def dt_text(dt):
    if dt is None:
        return ''
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def serialize_room(row, owner_openid):
    return {
        '_id': str(row['id']),
        'roomNo': row['room_no'],
        'status': STATUS_TEXT.get(row['status'], 'waiting'),
        'maxPlayers': row['max_players'],
        'ownerOpenid': owner_openid,
        'roomName': row.get('name') or '',
        'createdAt': dt_text(row['created_at']),
        'createTime': dt_ms(row['created_at']),
        'finishedAt': dt_text(row.get('finished_at')) if row.get('finished_at') else '',
    }


def serialize_member(row, is_owner=False):
    return {
        'openid': row['openid'],
        'nickName': row['nickname'],
        'avatarUrl': row['avatar_url'],
        'score': row['score'],
        'isOwner': bool(is_owner or row.get('role') == 0),
    }


def get_room_or_404(room_id=None, room_no=None):
    """连房主 openid 一起查出房间。"""
    if room_id is not None:
        where, value = 'r.id = %s', room_id
    elif room_no:
        where, value = 'r.room_no = %s', str(room_no).zfill(6)
    else:
        return None
    return fetchone(
        '''SELECT r.*, u.openid AS owner_openid
           FROM rooms r JOIN users u ON u.id = r.owner_id
           WHERE %s''' % where, (value,)
    )


def list_members(room_id):
    return fetchall(
        '''SELECT openid, nickname, avatar_url, score, role
           FROM room_members WHERE room_id=%s
           ORDER BY role ASC, joined_at ASC, id ASC''',
        (room_id,)
    )


@bp.route('/api/room/create', methods=['POST'])
@login_required
def create_room():
    """创建房间：生成 6 位房号，房主自动入房。"""
    body = request.get_json(silent=True) or {}
    nickname, avatar_url = _extract_profile(body)
    if not nickname:
        return fail('请先填写昵称')

    try:
        max_players = int(body.get('maxPlayers', 8))
    except (TypeError, ValueError):
        return fail('人数参数错误')
    if not 2 <= max_players <= 20:
        return fail('人数需在 2-20 之间')

    # 同步资料
    _upsert_user(g.user['openid'], g.user.get('union_id'), nickname, avatar_url)

    # 生成不冲突的 6 位房号（避开前导零也行，这里允许 100000-999999）
    room_no = None
    for _ in range(5):
        candidate = str(random.randint(100000, 999999))
        if not fetchone('SELECT id FROM rooms WHERE room_no = %s', (candidate,)):
            room_no = candidate
            break
    if room_no is None:
        return fail('房号繁忙，请重试')

    with db_cursor(commit=True) as cur:
        cur.execute(
            '''INSERT INTO rooms (room_no, owner_id, max_players, status)
               VALUES (%s, %s, %s, 0)''',
            (room_no, g.user['id'], max_players)
        )
        room_id = cur.lastrowid
        cur.execute(
            '''INSERT INTO room_members
                 (room_id, user_id, openid, nickname, avatar_url, role, score)
               VALUES (%s, %s, %s, %s, %s, 0, 0)''',
            (room_id, g.user['id'], g.user['openid'], nickname, avatar_url)
        )

    return ok({'roomId': str(room_id), 'roomNo': room_no})


@bp.route('/api/room/join', methods=['POST'])
@login_required
def join_room():
    """凭房号加入房间（仅 waiting 状态可加入，重复加入幂等）。"""
    body = request.get_json(silent=True) or {}
    room_no = body.get('roomNo')
    room_id = body.get('roomId')
    nickname, avatar_url = _extract_profile(body)

    room = get_room_or_404(room_id=room_id, room_no=room_no)
    if not room:
        return fail('房间不存在')
    if room['status'] != 0:
        return fail('房间已开始或已结束')

    if nickname:
        _upsert_user(g.user['openid'], g.user.get('union_id'), nickname, avatar_url)

    member = fetchone(
        'SELECT id FROM room_members WHERE room_id=%s AND user_id=%s',
        (room['id'], g.user['id'])
    )
    if member:
        # 已在房间：若带了新资料则更新快照
        if nickname:
            with db_cursor(commit=True) as cur:
                cur.execute(
                    '''UPDATE room_members SET nickname=%s, avatar_url=%s
                       WHERE id=%s''',
                    (nickname, avatar_url, member['id'])
                )
        return ok({'roomId': str(room['id']), 'roomNo': room['room_no']})

    members = list_members(room['id'])
    if len(members) >= room['max_players']:
        return fail('房间人数已满')

    snap_nick = nickname or g.user['nickname']
    snap_avatar = avatar_url or g.user['avatar_url']
    with db_cursor(commit=True) as cur:
        cur.execute(
            '''INSERT INTO room_members
                 (room_id, user_id, openid, nickname, avatar_url, role, score)
               VALUES (%s, %s, %s, %s, %s, 1, 0)''',
            (room['id'], g.user['id'], g.user['openid'], snap_nick, snap_avatar)
        )

    return ok({'roomId': str(room['id']), 'roomNo': room['room_no']})


def _require_owner(room):
    if room['owner_id'] != g.user['id']:
        return fail('仅房主可操作')
    return None


@bp.route('/api/room/start', methods=['POST'])
@login_required
def start_room():
    body = request.get_json(silent=True) or {}
    room = get_room_or_404(room_id=body.get('roomId'))
    if not room:
        return fail('房间不存在')
    err = _require_owner(room)
    if err:
        return err
    if room['status'] != 0:
        return fail('当前状态不可开始')

    with db_cursor(commit=True) as cur:
        cur.execute('UPDATE rooms SET status=1 WHERE id=%s AND status=0', (room['id'],))
        if cur.rowcount == 0:
            return fail('开始失败，请刷新重试')
    return ok()


@bp.route('/api/room/finish', methods=['POST'])
@login_required
def finish_room():
    body = request.get_json(silent=True) or {}
    room = get_room_or_404(room_id=body.get('roomId'))
    if not room:
        return fail('房间不存在')
    err = _require_owner(room)
    if err:
        return err
    if room['status'] == 2:
        return ok()  # 幂等
    if room['status'] != 1:
        return fail('房间尚未开始')

    with db_cursor(commit=True) as cur:
        cur.execute(
            'UPDATE rooms SET status=2, finished_at=NOW() WHERE id=%s AND status=1',
            (room['id'],)
        )
        if cur.rowcount == 0:
            return fail('结束失败，请刷新重试')
    return ok()


@bp.route('/api/room/detail', methods=['GET'])
def room_detail():
    """房间详情：分享链接打开时用户可能尚未加入，故不做成员校验。"""
    room_id = request.args.get('roomId')
    room_no = request.args.get('roomNo')
    room = get_room_or_404(room_id=room_id, room_no=room_no)
    if not room:
        return fail('房间不存在')

    members = [serialize_member(m, is_owner=(m['openid'] == room['owner_openid']))
               for m in list_members(room['id'])]
    return ok({
        'room': serialize_room(room, room['owner_openid']),
        'members': members,
    })


@bp.route('/api/rooms/mine', methods=['GET'])
@login_required
def my_rooms():
    """我的房间列表。

    type:   all(默认) / created(我创建的) / joined(我加入的，不含自己创建的)
    status: all(默认) / waiting / playing / finished
    """
    type_ = request.args.get('type', 'all')
    status_text = request.args.get('status', 'all')

    where = ['rm.user_id = %s']
    params = [g.user['id']]

    if type_ == 'created':
        where.append('r.owner_id = %s')
        params.append(g.user['id'])
    elif type_ == 'joined':
        where.append('r.owner_id <> %s')
        params.append(g.user['id'])

    if status_text in STATUS_CODE:
        where.append('r.status = %s')
        params.append(STATUS_CODE[status_text])

    sql = '''
        SELECT r.*, u.openid AS owner_openid
        FROM room_members rm
        JOIN rooms r ON r.id = rm.room_id
        JOIN users u ON u.id = r.owner_id
        WHERE %s
        ORDER BY r.id DESC
        LIMIT 50
    ''' % ' AND '.join(where)
    rooms = fetchall(sql, tuple(params))

    # 一次性拉全部成员，避免 N+1
    result = []
    if rooms:
        room_ids = [r['id'] for r in rooms]
        placeholders = ','.join(['%s'] * len(room_ids))
        members = fetchall(
            '''SELECT room_id, openid, nickname, avatar_url, score, role
               FROM room_members WHERE room_id IN (%s)
               ORDER BY role ASC, joined_at ASC, id ASC''' % placeholders,
            tuple(room_ids)
        )
        grouped = {}
        for m in members:
            grouped.setdefault(m['room_id'], []).append(m)

        for r in rooms:
            result.append({
                **serialize_room(r, r['owner_openid']),
                'members': [
                    serialize_member(m, is_owner=(m['openid'] == r['owner_openid']))
                    for m in grouped.get(r['id'], [])
                ],
            })

    return ok({'rooms': result})
