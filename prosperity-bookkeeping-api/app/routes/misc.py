# -*- coding: utf-8 -*-
"""其他接口：小程序码。"""
import base64

from flask import Blueprint, request

from ..auth import login_required
from ..db import fetchone
from ..response import fail, ok
from ..wechat import WeChatError, get_unlimited_qrcode

bp = Blueprint('misc', __name__)


@bp.route('/api/qrcode', methods=['POST'])
@login_required
def room_qrcode():
    """生成加入房间的小程序码，以 data URL 返回，前端可直接放入 image src。"""
    body = request.get_json(silent=True) or {}
    room_no = str(body.get('roomNo') or '').strip()
    if not room_no:
        return fail('缺少 roomNo')

    room = fetchone('SELECT id, owner_id FROM rooms WHERE room_no=%s', (room_no.zfill(6),))
    if not room:
        return fail('房间不存在')

    try:
        image = get_unlimited_qrcode(room_no.zfill(6))
    except WeChatError as e:
        return fail(str(e))

    data_url = 'data:image/png;base64,' + base64.b64encode(image).decode('ascii')
    return ok({'qrcodeUrl': data_url})
