# -*- coding: utf-8 -*-
"""回测/前向执行器：固定仓位法（每日买 top1，持仓自然重叠，逐日重估净值）"""
from __future__ import annotations

import numpy as np


def run_portfolio(mk, dsl: dict, start: int = 120, alloc: float = 0.02):
    """固定仓位组合回测。

    参数:
        alloc: 每笔投入的净值比例（默认 2%，同时持 ~15 笔时敞口约 30%）

    返回:
        trades   每笔交易记录（独立样本，用于统计信号质量）
        nav      日度净值序列 [{'date':..., 'nav':...}]
        metrics  汇总指标
    """
    mask = mk.signal(dsl['signal'])
    rank_cfg = dsl.get('rank_by')
    rank = (mk.indicator(rank_cfg['ind'], int(rank_cfg.get('window', 1)))
            if rank_cfg else None)
    n_per_day = int(dsl.get('entry', {}).get('max_per_day', 1))
    ex = dsl.get('exit', {})
    tp = float(ex.get('take_profit', 0.10))
    sl = float(ex.get('stop_loss', 0.07))
    hold = int(ex.get('max_hold_days', 30))
    cost = 0.00025 * 2 + 0.0005 + 0.001

    cash = 1.0
    open_pos: list[dict] = []
    trades: list[dict] = []
    nav_rows: list[dict] = []
    pending: list[int] = []          # 昨日信号选中的股票索引，今日开盘买入

    for t in range(max(start, 60), mk.m):
        # 1) 开盘执行昨日信号
        if pending:
            for i in pending:
                px = mk.O[i, t]
                if not np.isfinite(px) or px <= 0:
                    continue
                amt = cash * alloc
                if amt <= 0:
                    continue
                cash -= amt
                open_pos.append(dict(i=i, entry_px=float(px), entry_t=t,
                                     entry_date=int(mk.dates[t]),
                                     shares=amt / float(px)))
            pending = []

        # 2) 收盘检查出场
        still = []
        for p in open_pos:
            px = mk.C[p['i'], t]
            if not np.isfinite(px):
                still.append(p)
                continue
            r = px / p['entry_px'] - 1
            held = t - p['entry_t']
            reason = None
            if r >= tp:
                reason = 'take_profit'
            elif r <= -sl:
                reason = 'stop_loss'
            elif held >= hold:
                reason = 'time'
            if reason:
                gross = p['shares'] * float(px)
                cash += gross * (1 - cost)
                trades.append(dict(
                    code=mk.codes[p['i']],
                    entry_date=p['entry_date'],
                    entry_price=round(p['entry_px'], 3),
                    exit_date=int(mk.dates[t]),
                    exit_price=round(float(px), 3),
                    ret=round(float(r - cost), 5),
                    reason=reason,
                    hold_days=int(held) + 1,
                ))
            else:
                still.append(p)
        open_pos = still

        # 3) 收盘估值净值
        mv = 0.0
        for p in open_pos:
            px = mk.C[p['i'], t]
            mv += p['shares'] * (float(px) if np.isfinite(px) else p['entry_px'])
        nav_rows.append(dict(date=int(mk.dates[t]), nav=round(cash + mv, 6)))

        # 4) 生成次日待买入清单
        if t < mk.m - 1:
            cand = np.flatnonzero(mask[:, t])
            if cand.size:
                if rank is not None:
                    rk = rank[cand, t]
                    cand = cand[np.argsort(-np.nan_to_num(rk, nan=-9e9))]
                pending = list(cand[:n_per_day])

    metrics = summarize(trades, nav_rows)
    return trades, nav_rows, metrics


def summarize(trades, nav_rows) -> dict:
    if not trades:
        return dict(trades=0, win_rate=0.0, total_return=0.0, avg_return=0.0,
                    profit_factor=0.0, avg_hold=0.0, max_drawdown=0.0,
                    max_loss=0.0, median_return=0.0, exposure=0.0)
    r = np.array([x['ret'] for x in trades], dtype=float)
    wins, losses = r[r > 0], r[r <= 0]
    nav = np.array([x['nav'] for x in nav_rows], dtype=float)
    peak = np.maximum.accumulate(nav)
    dd = float((nav / peak - 1).min()) if nav.size else 0.0
    return dict(
        trades=int(len(r)),
        win_rate=round(float((r > 0).mean()), 4),
        total_return=round(float(nav[-1] - 1), 4) if nav.size else 0.0,
        avg_return=round(float(r.mean()), 5),
        median_return=round(float(np.median(r)), 5),
        profit_factor=round(float(wins.sum() / max(-losses.sum(), 1e-9)), 3),
        avg_hold=round(float(np.mean([x['hold_days'] for x in trades])), 1),
        max_drawdown=round(dd, 4),
        max_loss=round(float(r.min()), 4),
        max_gain=round(float(r.max()), 4),
    )
