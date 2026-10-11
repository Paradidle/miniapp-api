# -*- coding: utf-8 -*-
"""路由注册。"""
from . import auth, misc, room, score


def register_routes(app):
    app.register_blueprint(auth.bp)
    app.register_blueprint(room.bp)
    app.register_blueprint(score.bp)
    app.register_blueprint(misc.bp)
