# -*- coding: utf-8 -*-
"""FastAPI 入口：策略管理 + 赛马图数据 + 回测/每日任务。"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .compiler import UnsupportedIndicator, compile_strategy, INDICATORS
from .engine import Market
from .store import Repo

VIPDOC = os.environ.get('TDX_VIPDOC', '/data/vipdoc')
DATA = os.environ.get('RACE_DATA', './data')
WEB = Path(__file__).resolve().parent.parent / 'web'

app = FastAPI(title='策略赛马场 API', version='0.1.0')
repo = Repo(DATA)
_market: Market | None = None


def market() -> Market:
    """首次调用时加载全市场矩阵（约 2-5 秒）"""
    global _market
    if _market is None:
        limit = os.environ.get('RACE_LIMIT')
        _market = Market(VIPDOC, limit=int(limit) if limit else None)
    return _market


# ---------------- 模型 ----------------
class Cond(BaseModel):
    ind: str
    window: int = 1
    op: str
    value: float | list[float]


class DSL(BaseModel):
    signal: list[Cond] = Field(..., min_length=1)
    rank_by: dict | None = None
    entry: dict = {'max_per_day': 1}
    exit: dict = {'take_profit': 0.05, 'stop_loss': 0.12, 'max_hold_days': 30}


class StrategyIn(BaseModel):
    name: str
    raw_text: str = ''
    interpreted_cn: str = ''
    dsl: DSL


# ---------------- 路由 ----------------
@app.get('/healthz')
def healthz():
    """Jenkins 健康检查：不加载行情数据，立即返回"""
    return {'status': 'ok'}

@app.get('/api/health')
def health():
    mk = market()
    return dict(
        status='ok',
        vipdoc=VIPDOC,
        stocks=mk.n,
        days=mk.m,
        first_date=int(mk.dates[0]),
        last_date=int(mk.dates[-1]),
        server_time=datetime.now().isoformat(timespec='seconds'),
    )


@app.get('/api/strategies')
def list_strategies():
    return repo.list_meta()


@app.post('/api/strategies')
def create_strategy(body: StrategyIn):
    sid = 's_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:19]
    meta = dict(id=sid, name=body.name, raw_text=body.raw_text,
                interpreted_cn=body.interpreted_cn,
                dsl=body.dsl.model_dump(),
                created_at=datetime.now().isoformat(timespec='seconds'))
    repo.save_meta(meta)
    return meta


class CompileIn(BaseModel):
    text: str
    run_backtest: bool = True


@app.get('/api/indicators')
def indicators():
    """可用指标白名单（前端展示用）"""
    return {k: v['desc'] for k, v in INDICATORS.items()}


@app.post('/api/strategies/compile')
def compile_only(body: CompileIn):
    """只编译不落库，返回 DSL 让用户确认"""
    try:
        return compile_strategy(body.text)
    except UnsupportedIndicator as e:
        raise HTTPException(422, {'message': '暂不支持该指标', 'items': e.items})


@app.post('/api/strategies/from-text')
def from_text(body: CompileIn):
    """自然语言一键建策略（编译 + 落库 + 可选回测）"""
    try:
        r = compile_strategy(body.text)
    except UnsupportedIndicator as e:
        raise HTTPException(422, {'message': '暂不支持该指标', 'items': e.items})
    sid = 's_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:19]
    meta = dict(id=sid, name=r['name'], raw_text=body.text,
                interpreted_cn=r['interpreted_cn'], dsl=r['dsl'],
                source=r.get('source'), created_at=datetime.now().isoformat(timespec='seconds'))
    repo.save_meta(meta)
    out = dict(meta=meta)
    if body.run_backtest:
        trades, metrics = market().run(r['dsl'])
        repo.save_trades(sid, 'backtest', trades)
        repo.save_metrics(sid, 'backtest', metrics)
        out['metrics'] = metrics
    return out


@app.get('/api/strategies/{sid}')
def get_strategy(sid: str):
    meta = repo.load_meta(sid)
    if not meta:
        raise HTTPException(404, '策略不存在')
    meta['live'] = repo.load_metrics(sid, 'live')
    meta['backtest'] = repo.load_metrics(sid, 'backtest')
    return meta


@app.post('/api/strategies/{sid}/backtest')
def run_backtest(sid: str):
    meta = repo.load_meta(sid)
    if not meta:
        raise HTTPException(404, '策略不存在')
    trades, metrics = market().run(meta['dsl'])
    repo.save_trades(sid, 'backtest', trades)
    repo.save_metrics(sid, 'backtest', metrics)
    return dict(metrics=metrics, sample=trades[:5])


@app.get('/api/strategies/{sid}/trades')
def get_trades(sid: str, kind: str = 'live', page: int = 1, size: int = 50):
    if kind not in ('live', 'backtest'):
        raise HTTPException(400, 'kind 只能是 live 或 backtest')
    return repo.read_trades(sid, kind, page, size)


@app.get('/api/ranking')
def ranking():
    return dict(updated_at=datetime.now().isoformat(timespec='seconds'),
                rows=repo.ranking())


@app.post('/api/admin/run-daily')
def run_daily():
    """前向任务：用最新一根 K 线出信号，生成次日买入清单并记录"""
    mk = market()
    out = []
    for meta in repo.list_meta():
        picks = mk.today_signals(meta['dsl'], top=1)
        out.append(dict(id=meta['id'], name=meta['name'], picks=picks))
    return dict(date=int(mk.dates[-1]), results=out)


# 静态前端
if WEB.is_dir():
    app.mount('/static', StaticFiles(directory=str(WEB)), name='static')

    @app.get('/')
    def index():
        return FileResponse(str(WEB / 'index.html'))
