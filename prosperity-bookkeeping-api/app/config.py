# -*- coding: utf-8 -*-
"""全局配置：读取项目根目录下的 config.ini（本机部署改这个文件即可）。

取值优先级：环境变量 > config.ini > 代码默认值。
环境变量覆盖是为了兼容 Docker/云托管部署（平台注入 MYSQL_ADDRESS 等），
直接部署在自有服务器时无需配置任何环境变量。
"""
import os
from configparser import ConfigParser

# 项目根目录（本文件在 <根目录>/app/config.py）
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_FILE = os.path.join(_BASE_DIR, 'config.ini')

# interpolation=None：密码里即使含 % 也按原文读取，不做插值解析
_parser = ConfigParser(interpolation=None)
if os.path.exists(_CONFIG_FILE):
    _parser.read(_CONFIG_FILE, encoding='utf-8')


def _cfg(section, option, default='', env=None):
    """字符串配置：环境变量优先，其次 config.ini，最后默认值。"""
    if env and env in os.environ:
        return os.environ[env].strip()
    if _parser.has_option(section, option):
        return _parser.get(section, option).strip()
    return default


def _cfgi(section, option, default, env=None):
    """整数配置。"""
    value = _cfg(section, option, '', env=env)
    return int(value) if value else default


def _mysql_host_port():
    """MySQL 地址：兼容云托管注入的 MYSQL_ADDRESS=host:port。"""
    address = os.environ.get('MYSQL_ADDRESS', '').strip()
    if address:
        if ':' in address:
            host, port = address.rsplit(':', 1)
            return host.strip(), int(port)
        return address, _cfgi('mysql', 'port', 3306, env='MYSQL_PORT')
    return (_cfg('mysql', 'host', '127.0.0.1', env='MYSQL_HOST'),
            _cfgi('mysql', 'port', 3306, env='MYSQL_PORT'))


_mysql_host, _mysql_port = _mysql_host_port()

# 上传目录默认 <项目根目录>/uploads
_default_upload_dir = os.path.join(_BASE_DIR, 'uploads')


class Config:
    # ---------- MySQL ----------
    MYSQL_HOST = _mysql_host
    MYSQL_PORT = _mysql_port
    MYSQL_USER = _cfg('mysql', 'user', 'root', env='MYSQL_USERNAME')
    MYSQL_PASSWORD = _cfg('mysql', 'password', '', env='MYSQL_PASSWORD')
    MYSQL_DATABASE = _cfg('mysql', 'database', 'prosperity_bookkeeping', env='MYSQL_DATABASE')
    MYSQL_CHARSET = _cfg('mysql', 'charset', 'utf8mb4')

    # 连接池（低内存：常驻连接尽量少）
    DB_MIN_CACHED = _cfgi('db_pool', 'min_cached', 0, env='DB_MIN_CACHED')
    DB_MAX_CACHED = _cfgi('db_pool', 'max_cached', 4, env='DB_MAX_CACHED')
    DB_MAX_CONNECTIONS = _cfgi('db_pool', 'max_connections', 10, env='DB_MAX_CONNECTIONS')

    # ---------- 微信小程序 ----------
    WX_APPID = _cfg('wechat', 'appid', '', env='WX_APPID')
    WX_SECRET = _cfg('wechat', 'secret', '', env='WX_SECRET')
    # 小程序码版本：release 正式版 / trial 体验版 / develop 开发版
    WX_ENV_VERSION = _cfg('wechat', 'env_version', 'release', env='WX_ENV_VERSION')

    # ---------- 业务 ----------
    # 登录态签名密钥
    TOKEN_SECRET = _cfg('business', 'token_secret', 'dev-only-change-this-secret', env='TOKEN_SECRET')
    TOKEN_EXPIRE_SECONDS = _cfgi('business', 'token_expire_seconds', 30 * 24 * 3600,
                                 env='TOKEN_EXPIRE_SECONDS')

    # nginx 反代路径前缀（location /prosperity-bookkeeping-api/），
    # 与前端 utils/api.js 的 BASE_PATH 保持一致；后端自动剥离，直连不受影响
    URL_PREFIX = _cfg('business', 'url_prefix', '/prosperity-bookkeeping-api', env='URL_PREFIX')

    # 上传文件目录（头像等），默认项目根目录下 uploads/（ini 中留空也走默认）
    UPLOAD_DIR = _cfg('business', 'upload_dir', '', env='UPLOAD_DIR') or _default_upload_dir
    MAX_UPLOAD_MB = _cfgi('business', 'max_upload_mb', 2)
