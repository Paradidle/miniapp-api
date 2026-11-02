# -*- coding: utf-8 -*-
"""文件存储：策略目录 + JSON，原子写，无数据库。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, encoding='utf-8') as f:
        return json.load(f)


class Repo:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        (self.root / 'strategies').mkdir(parents=True, exist_ok=True)

    def _d(self, sid: str) -> Path:
        return self.root / 'strategies' / sid

    # ---- meta ----
    def save_meta(self, meta: dict) -> None:
        atomic_write(self._d(meta['id']) / 'meta.json', meta)

    def load_meta(self, sid: str):
        return read_json(self._d(sid) / 'meta.json')

    def list_meta(self):
        out = []
        for d in sorted((self.root / 'strategies').iterdir()):
            if not d.is_dir():
                continue
            meta = read_json(d / 'meta.json')
            if not meta:
                continue
            meta['live'] = read_json(d / 'live_metrics.json', {})
            meta['backtest'] = read_json(d / 'bt_metrics.json', {})
            out.append(meta)
        return out

    # ---- metrics ----
    def save_metrics(self, sid: str, kind: str, metrics: dict) -> None:
        name = 'live_metrics.json' if kind == 'live' else 'bt_metrics.json'
        atomic_write(self._d(sid) / name, metrics)

    def load_metrics(self, sid: str, kind: str):
        name = 'live_metrics.json' if kind == 'live' else 'bt_metrics.json'
        return read_json(self._d(sid) / name, {})

    # ---- trades ----
    def save_trades(self, sid: str, kind: str, trades: list) -> None:
        name = 'live_trades.json' if kind == 'live' else 'bt_trades.json'
        atomic_write(self._d(sid) / name, trades)

    def append_trades(self, sid: str, trades: list) -> int:
        """前向交易追加，按 (code, entry_date) 去重"""
        p = self._d(sid) / 'live_trades.json'
        old = read_json(p, [])
        seen = {(t['code'], t['entry_date']) for t in old}
        added = [t for t in trades if (t['code'], t['entry_date']) not in seen]
        if added:
            atomic_write(p, old + added)
        return len(added)

    def save_nav(self, sid: str, rows: list) -> None:
        atomic_write(self._d(sid) / "bt_nav.json", rows)

    def load_nav(self, sid: str):
        return read_json(self._d(sid) / "bt_nav.json", [])

    def read_trades(self, sid: str, kind: str, page: int = 1, size: int = 50):
        name = 'live_trades.json' if kind == 'live' else 'bt_trades.json'
        all_t = read_json(self._d(sid) / name, [])
        total = len(all_t)
        start = max(0, (page - 1) * size)
        return dict(total=total, page=page, size=size,
                    items=all_t[start:start + size])

    def ranking(self):
        """排行榜：以前向平均每笔收益率为首要排序依据。

        口径说明（等额投资）：avg_return = 平均每笔收益率（核心指标）
                             total_return = 累计盈亏（倍单笔本金）
        """
        rows = []
        for m in self.list_meta():
            live = m.get('live') or {}
            bt = m.get('backtest') or {}
            rows.append(dict(
                id=m['id'], name=m.get('name', ''),
                # 前向（赛马图）
                live_trades=live.get('trades', 0),
                live_win_rate=live.get('win_rate', 0),
                live_avg=live.get('avg_return', 0),
                live_total=live.get('total_return', 0),
                # 回测（详情页参考）
                bt_trades=bt.get('trades', 0),
                bt_win_rate=bt.get('win_rate', 0),
                bt_avg=bt.get('avg_return', 0),
                bt_pf=bt.get('profit_factor', 0),
                bt_max_loss=bt.get('max_loss', 0),
                bt_total=bt.get('total_return', 0),
            ))
        # 优先按前向平均收益率，前向样本不足时退回回测
        rows.sort(key=lambda r: (-(r['live_avg'] if r['live_trades'] >= 10 else r['bt_avg'])))
        return rows
