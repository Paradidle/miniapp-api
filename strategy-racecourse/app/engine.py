# -*- coding: utf-8 -*-
"""数据引擎：读 .day、构建矩阵、计算指标、执行 DSL 信号、回测/前向。"""
from __future__ import annotations

import os
from pathlib import Path

import warnings

import numpy as np

warnings.filterwarnings('ignore', message='.*All-NaN slice.*')
warnings.filterwarnings('ignore', message='.*Mean of empty slice.*')

from .filters import load_stock_base

REC_SIZE = 32
DT = np.dtype([('date', '<u4'), ('o', '<u4'), ('h', '<u4'), ('l', '<u4'),
               ('c', '<u4'), ('amt', '<f4'), ('vol', '<u4'), ('r', '<u4')])

UNIVERSE = (('sh', ('600', '601', '603', '605')),
            ('sz', ('000', '001', '002', '003')))

# 交易成本：佣金双边 + 印花税卖出 + 滑点
COST = 0.00025 * 2 + 0.0005 + 0.001


# flat 布局：文件名本身含反斜杠（Windows zip 解压到 Linux 的典型产物）
FLAT_PROBE = 'sh\\lday\\sh000001.day'


def resolve_vipdoc(p: str | Path) -> Path:
    """兼容两种布局，返回数据目录（不含是否 flat 的信息，供旧调用方使用）"""
    return detect_layout(p)[0]


def detect_layout(p: str | Path):
    """探测数据布局。

    返回 (目录, is_flat)：
        is_flat=False  标准嵌套：<dir>/sh/lday/sh000001.day
        is_flat=True   扁平：<dir>/'sh\\lday\\sh000001.day'（反斜杠是文件名的一部分）
    """
    p = Path(p)
    cands = [p, p / 'vipdoc', p.parent / 'vipdoc', p.parent]
    nested, flat = [], []
    for c in cands:
        try:
            if (c / 'sh' / 'lday' / 'sh000001.day').exists():
                return c, False
            nested.append(str(c))
            if (c / FLAT_PROBE).exists():
                return c, True
            # 探测文件缺失时，扫描是否存在任何 'xx\\lday\\*.day'
            try:
                if any('\\lday\\' in f.name and f.name.endswith('.day')
                       for f in c.iterdir()):
                    return c, True
            except OSError:
                pass
            flat.append(str(c / FLAT_PROBE))
        except OSError:
            continue
    raise FileNotFoundError(
        '找不到通达信日线数据，已尝试：\n'
        + '  嵌套布局:\n    - ' + '\n    - '.join(nested)
        + '\n  扁平布局:\n    - ' + '\n    - '.join(flat)
        + '\n请检查环境变量 TDX_VIPDOC。'
    )


def read_day(path: str | Path) -> np.ndarray:
    raw = Path(path).read_bytes()
    if len(raw) % REC_SIZE:
        raise ValueError(f'{path}: 大小非 32 倍数')
    return np.frombuffer(raw, dtype=DT)


def flat_pairs(vipdoc: Path):
    """扫描 flat 布局目录，返回 [(code, 原始文件名)]。

    文件名形如 'sh\\lday\\sh600000.day'（反斜杠是文件名的一部分）。
    这是纯函数，便于单元测试。
    """
    out = []
    try:
        entries = sorted(vipdoc.iterdir(), key=lambda f: f.name)
    except OSError:
        return out
    for f in entries:
        name = f.name
        if '\\' not in name or not name.endswith('.day'):
            continue
        parts = name.split('\\')
        if len(parts) != 3 or parts[1] != 'lday':
            continue
        mk, code = parts[0], parts[2][:-4]
        for m, prefixes in UNIVERSE:
            if mk == m and code[:2] == mk and code[2:5] in prefixes:
                out.append((code, name))
                break
    return out


def list_codes(vipdoc: Path, flat: bool = False):
    out = []
    if flat:
        for code, name in flat_pairs(vipdoc):
            out.append((code, vipdoc / name))
        return out
    for mk, prefixes in UNIVERSE:
        d = vipdoc / mk / 'lday'
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if not f.name.endswith('.day'):
                continue
            code = f.name[:-4]
            if code[:2] == mk and code[2:5] in prefixes:
                out.append((code, f))
    return out


class Market:
    """全市场矩阵。一次加载，常驻内存。"""

    def __init__(self, vipdoc: str | Path, limit: int | None = None):
        self.vipdoc, self.flat = detect_layout(vipdoc)
        idx_path = (self.vipdoc / FLAT_PROBE) if self.flat \
            else (self.vipdoc / 'sh' / 'lday' / 'sh000001.day')
        idx = read_day(idx_path)
        idx = idx[idx['date'] > 0]
        idx = idx[np.argsort(idx['date'])]
        self.dates = idx['date'].astype(np.int64)
        m = len(self.dates)

        files = list_codes(self.vipdoc, self.flat)
        if limit:
            files = files[:limit]
        n = len(files)
        self.codes = [c for c, _ in files]

        self.O = np.full((n, m), np.nan, np.float32)
        self.H = np.full((n, m), np.nan, np.float32)
        self.L = np.full((n, m), np.nan, np.float32)
        self.C = np.full((n, m), np.nan, np.float32)
        self.V = np.full((n, m), np.nan, np.float32)
        self.A = np.full((n, m), np.nan, np.float32)

        for i, (_, p) in enumerate(files):
            a = read_day(p)
            a = a[a['date'] > 0]
            if len(a) == 0:
                continue
            pos = np.searchsorted(self.dates, a['date'].astype(np.int64))
            ok = (pos < m) & (self.dates[np.clip(pos, 0, m - 1)] == a['date'].astype(np.int64))
            pos = pos[ok]
            self.O[i, pos] = a['o'][ok] / 100.0
            self.H[i, pos] = a['h'][ok] / 100.0
            self.L[i, pos] = a['l'][ok] / 100.0
            self.C[i, pos] = a['c'][ok] / 100.0
            self.V[i, pos] = a['vol'][ok]
            self.A[i, pos] = a['amt'][ok]

        self.n, self.m = n, m
        self._cache: dict[tuple, np.ndarray] = {}
        # 可交易性：T+1 开盘存在且非一字涨停
        gap = np.full((n, m), np.nan, np.float32)
        gap[:, 1:] = self.O[:, 1:] / self.C[:, :-1] - 1
        self.can_buy = np.isfinite(self.O) & ~(np.roll(gap, -1, axis=1) >= 0.095)
        self.can_buy[:, -1] = False

        # ---- 风险过滤基础数据（上市日期 / 流通股本）----
        dbf = os.environ.get('TDX_BASE_DBF') or str(
            self.vipdoc.parent / 'T0002' / 'hq_cache' / 'base.dbf')
        base = load_stock_base(dbf)
        self.list_idx = np.zeros(n, dtype=np.int64)
        self.float_sh = np.zeros(n, dtype=np.float32)
        for k, c in enumerate(self.codes):
            info = base.get(c)
            if not info:
                continue
            ld = info.get('list_date') or 0
            if ld:
                self.list_idx[k] = np.searchsorted(self.dates, ld)
            self.float_sh[k] = info.get('float_shares') or 0.0
        self._risk = None

    # ---------- 指标 ----------
    def _roll(self, X, w, f):
        out = np.full(X.shape, np.nan, np.float32)
        for t in range(w, X.shape[1]):
            out[:, t] = f(X[:, t - w:t])
        return out

    def indicator(self, name: str, window: int = 1) -> np.ndarray:
        key = (name, window)
        if key in self._cache:
            return self._cache[key]
        C, O, H, L, V, A = self.C, self.O, self.H, self.L, self.V, self.A
        w = max(1, int(window))
        with np.errstate(invalid='ignore', divide='ignore'):
            if name == 'close':
                v = C
            elif name == 'ret':
                v = np.full(C.shape, np.nan, np.float32)
                v[:, w:] = C[:, w:] / C[:, :-w] - 1
            elif name == 'ma':
                v = self._roll(C, w, lambda x: x.mean(axis=1))
            elif name == 'close_to_ma':
                ma = self.indicator('ma', w)
                v = C / ma - 1
            elif name == 'vol_ratio':
                base = self._roll(V, w, lambda x: np.nanmean(x, axis=1))
                v = V / base
            elif name == 'amount_ratio':
                base = self._roll(A, w, lambda x: np.nanmean(x, axis=1))
                v = A / base
            elif name == 'high_n':
                v = self._roll(H, w, lambda x: np.nanmax(x, axis=1))
            elif name == 'low_n':
                v = self._roll(L, w, lambda x: np.nanmin(x, axis=1))
            elif name == 'breakout':
                hn = self.indicator('high_n', w)
                v = C / hn - 1
            elif name == 'box_position':
                hn = self.indicator('high_n', w)
                ln = self.indicator('low_n', w)
                v = (C - ln) / (hn - ln)
            elif name == 'shadow_up':
                v = (H - np.maximum(O, C)) / C
            elif name == 'shadow_down':
                v = (np.minimum(O, C) - L) / C
            elif name == 'amplitude':
                hn = self.indicator('high_n', w)
                ln = self.indicator('low_n', w)
                v = (hn - ln) / C
            elif name == 'limit_up':
                r = self.indicator('ret', 1)
                v = (r >= 0.098).astype(np.float32)
            else:
                raise ValueError(f'不支持的指标: {name}')
        v = v.astype(np.float32)
        self._cache[key] = v
        return v

    def risk_mask(self) -> np.ndarray:
        """风险过滤：次新股 / 低价股 / 小市值。

        原则：**数据缺失时放行，不误杀**。
        - 没有 base.dbf（拿不到流通股本/上市日期）时，相应条件自动跳过
        - 环境变量 RACE_RISK=off 可整体关闭
        """
        if self._risk is not None:
            return self._risk
        if str(os.environ.get('RACE_RISK', 'on')).lower() in ('off', '0', 'false'):
            self._risk = np.ones((self.n, self.m), dtype=bool)
            return self._risk

        days = float(os.environ.get('RACE_MIN_LISTED', 120))
        min_px = float(os.environ.get('RACE_MIN_PRICE', 2.0))
        min_mv = float(os.environ.get('RACE_MIN_FLOAT_MV', 20.0))

        ok = np.ones((self.n, self.m), dtype=bool)
        idx = np.arange(self.m)[None, :]

        # 1) 次新股：只有拿到上市日期才过滤
        has_list = self.list_idx > 0
        if has_list.any():
            listed = idx - self.list_idx[:, None]
            ok &= (~has_list[:, None]) | (listed >= days)

        with np.errstate(invalid='ignore'):
            # 2) 低价股：价格总是有的
            ok &= self.C >= min_px

            # 3) 小市值：只有拿到流通股本才过滤
            has_fs = self.float_sh > 0
            mv = self.float_sh[:, None] * self.C / 1e4
            ok &= (~has_fs[:, None]) | (mv >= min_mv)

        self._risk = ok
        return ok

    # ---------- DSL ----------
    def signal(self, conds) -> np.ndarray:
        mask = np.ones((self.n, self.m), dtype=bool)
        for c in conds:
            v = self.indicator(c['ind'], int(c.get('window', 1)))
            op, val = c['op'], c['value']
            if op == '>=':
                mask &= v >= val
            elif op == '>':
                mask &= v > val
            elif op == '<=':
                mask &= v <= val
            elif op == '<':
                mask &= v < val
            elif op == 'between':
                lo, hi = val
                mask &= (v >= lo) & (v <= hi)
            else:
                raise ValueError(f'不支持的算子: {op}')
        mask &= self.can_buy & self.risk_mask()
        return mask

    # ---------- 回测 / 前向 ----------
    def run(self, dsl: dict, start: int = 120):
        """返回 (trades, equity_curve)。每交易日取 top1。"""
        mask = self.signal(dsl['signal'])
        rank_cfg = dsl.get('rank_by')
        rank = (self.indicator(rank_cfg['ind'], int(rank_cfg.get('window', 1)))
                if rank_cfg else None)
        n_per_day = int(dsl.get('entry', {}).get('max_per_day', 1))
        ex = dsl.get('exit', {})
        tp = float(ex.get('take_profit', 0.10))
        sl = float(ex.get('stop_loss', 0.07))
        hold = int(ex.get('max_hold_days', 30))

        trades = []
        for t in range(max(start, 60), self.m - 1):
            cand = np.flatnonzero(mask[:, t])
            if cand.size == 0:
                continue
            if rank is not None:
                r = rank[cand, t]
                cand = cand[np.argsort(-np.nan_to_num(r, nan=-9e9))]
            for i in cand[:n_per_day]:
                entry = self.O[i, t + 1]
                if not np.isfinite(entry) or entry <= 0:
                    continue
                end = min(t + 1 + hold, self.m - 1)
                seg = self.C[i, t + 1:end + 1]
                if seg.size == 0:
                    continue
                tp_hit = seg >= entry * (1 + tp)
                sl_hit = seg <= entry * (1 - sl)
                hit = tp_hit | sl_hit
                if hit.any():
                    k = int(np.argmax(hit))
                    reason = 'take_profit' if tp_hit[k] else 'stop_loss'
                else:
                    k = len(seg) - 1
                    reason = 'time'
                px = seg[k]
                if not np.isfinite(px):
                    continue
                trades.append(dict(
                    code=self.codes[i],
                    entry_date=int(self.dates[t + 1]),
                    entry_price=round(float(entry), 3),
                    exit_date=int(self.dates[t + 1 + k]),
                    exit_price=round(float(px), 3),
                    ret=round(float(px / entry - 1 - COST), 5),
                    reason=reason,
                    hold_days=k + 1,
                ))
        return trades, self._metrics(trades)

    @staticmethod
    def _metrics(trades):
        """等额投资口径：每笔投入相同金额，总收益 = 各笔收益率之和。"""
        if not trades:
            return dict(trades=0, win_rate=0.0, total_return=0.0, avg_return=0.0,
                        median_return=0.0, profit_factor=0.0, avg_hold=0.0,
                        max_loss=0.0, max_gain=0.0, max_drawdown=0.0)
        r = np.array([x['ret'] for x in trades], dtype=float)
        wins, losses = r[r > 0], r[r <= 0]
        cum = np.cumsum(r)
        peak = np.maximum.accumulate(cum)
        dd = float((cum - peak).min())          # 累计收益曲线的最大回撤（单位：倍本金）
        return dict(
            trades=int(len(r)),
            win_rate=round(float((r > 0).mean()), 4),
            total_return=round(float(r.sum()), 4),      # 累计收益率（等额投资）
            avg_return=round(float(r.mean()), 5),       # 平均每笔收益率
            median_return=round(float(np.median(r)), 4),
            profit_factor=round(float(wins.sum() / max(-losses.sum(), 1e-9)), 3),
            avg_hold=round(float(np.mean([x['hold_days'] for x in trades])), 1),
            max_loss=round(float(r.min()), 4),
            max_gain=round(float(r.max()), 4),
            max_drawdown=round(dd, 4),
        )

    def today_signals(self, dsl: dict, top: int = 10):
        """用最后一根 K 线出信号，返回候选清单（供次日执行）"""
        mask = self.signal(dsl['signal'])
        t = self.m - 1
        cand = np.flatnonzero(mask[:, t])
        rank_cfg = dsl.get('rank_by')
        if rank_cfg and cand.size:
            rank = self.indicator(rank_cfg['ind'], int(rank_cfg.get('window', 1)))
            cand = cand[np.argsort(-np.nan_to_num(rank[cand, t], nan=-9e9))]
        return [dict(code=self.codes[i],
                     close=round(float(self.C[i, t]), 3),
                     date=int(self.dates[t])) for i in cand[:top]]
