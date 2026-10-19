# -*- coding: utf-8 -*-
"""其他接口：小程序码、头像上传。"""
import base64
import os
import time
import uuid

from flask import Blueprint, request

from ..auth import login_required
from ..config import Config
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


# 头像允许的扩展名白名单
_AVATAR_EXTS = ('jpeg', 'jpg', 'png', 'webp')


@bp.route('/api/avatar/upload', methods=['POST'])
@login_required
def avatar_upload():
    """头像上传：前端压缩后以 base64 经 callContainer 传来（wx.uploadFile 需备案域名，走不通）。

    落盘 uploads/avatars/（与 nginx /uploads/ 静态目录一致），
    返回可直接给 <image> 用的 http URL，入库与展示均不再依赖云存储。
    """
    body = request.get_json(silent=True) or {}
    data_b64 = str(body.get('data') or '')
    ext = str(body.get('ext') or 'jpeg').lower().strip('.')

    # 兼容 dataURL 前缀（data:image/jpeg;base64,xxxx）
    if data_b64.startswith('data:'):
        data_b64 = data_b64.split(',', 1)[-1]

    if ext not in _AVATAR_EXTS:
        return fail('不支持的图片格式')
    if not data_b64:
        return fail('缺少图片数据')

    try:
        raw = base64.b64decode(data_b64, validate=True)
    except Exception:
        return fail('图片数据无效')
    if not raw:
        return fail('图片数据无效')
    if len(raw) > Config.MAX_UPLOAD_MB * 1024 * 1024:
        return fail('图片过大，请重新选择')

    avatars_dir = os.path.join(Config.UPLOAD_DIR, 'avatars')
    try:
        os.makedirs(avatars_dir, exist_ok=True)
        # 文件名服务端生成，不信任前端传入的任何路径信息
        filename = '%d-%s.%s' % (int(time.time() * 1000), uuid.uuid4().hex[:8], ext)
        with open(os.path.join(avatars_dir, filename), 'wb') as f:
            f.write(raw)
    except Exception:
        bp.logger.exception('保存头像文件失败')
        return fail('头像保存失败，请重试')

    url = '%s/uploads/avatars/%s' % (Config.PUBLIC_BASE_URL.rstrip('/'), filename)
    return ok({'url': url})
