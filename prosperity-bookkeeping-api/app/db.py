# -*- coding: utf-8 -*-
"""MySQL 连接池与轻量查询封装（PyMySQL + DBUtils，不使用重型 ORM）。"""
from contextlib import contextmanager

import pymysql
from dbutils.pooled_db import PooledDB

from .config import Config

pool = PooledDB(
    creator=pymysql,
    mincached=Config.DB_MIN_CACHED,
    maxcached=Config.DB_MAX_CACHED,
    maxconnections=Config.DB_MAX_CONNECTIONS,
    blocking=True,          # 连接数用完时等待而不是报错
    ping=1,                 # 取连接时做一次轻量 ping，避免被服务端断开
    host=Config.MYSQL_HOST,
    port=Config.MYSQL_PORT,
    user=Config.MYSQL_USER,
    password=Config.MYSQL_PASSWORD,
    database=Config.MYSQL_DATABASE,
    charset=Config.MYSQL_CHARSET,
    connect_timeout=5,
    autocommit=False,
    cursorclass=pymysql.cursors.DictCursor,
)


@contextmanager
def db_cursor(commit=False):
    """获取游标的上下文管理器。

    commit=True 时正常退出自动提交；任何异常均回滚。
    连接 close 后实际是归还连接池，不是物理断开。
    """
    conn = pool.connection()
    cur = conn.cursor()
    try:
        yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def fetchone(sql, args=None):
    with db_cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchone()


def fetchall(sql, args=None):
    with db_cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def execute(sql, args=None):
    """写操作（自动提交），返回 lastrowid。"""
    with db_cursor(commit=True) as cur:
        cur.execute(sql, args)
        return cur.lastrowid
