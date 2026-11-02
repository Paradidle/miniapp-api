# -*- coding: utf-8 -*-
"""每日任务：数据就绪检查 → 逐策略跑前向 → 落盘

用法:
    python -m app.daily              # 正常执行
    python -m app.daily --dry-run    # 只检查不写
    python -m app.daily --sid s_xxx  # 只跑指定策略

退出码: 0 成功  1 数据未就绪  2 有策略失败
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime

from .engine import Market
from .store import Repo

log = logging.getLogger('racecourse.daily')


def setup_logging(verbose=False):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format='%(asctime)s %(levelname)-7s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        stream=sys.stdout,
    )


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--sid', help='只跑指定策略')
    ap.add_argument('--force', action='store_true', help='数据未更新也跑')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args(argv)
    setup_logging(args.verbose)

    vipdoc = os.environ.get('TDX_VIPDOC', '/data/vipdoc')
    data_dir = os.environ.get('RACE_DATA', './data')

    t0 = time.time()
    log.info('=== 每日任务开始 ===')
    log.info('vipdoc=%s  data=%s', vipdoc, data_dir)

    if not os.path.isdir(vipdoc):
        log.error('数据目录不存在: %s', vipdoc)
        return 1

    repo = Repo(data_dir)
    metas = repo.list_meta()
    if args.sid:
        metas = [m for m in metas if m['id'] == args.sid]
    if not metas:
        log.warning('没有待执行策略')
        return 0

    # ---- 数据就绪检查 ----
    limit = os.environ.get('RACE_LIMIT')
    mk = Market(vipdoc, limit=int(limit) if limit else None)
    last = int(mk.dates[-1])
    today = int(datetime.now().strftime('%Y%m%d'))
    log.info('数据: %d 只 × %d 天, 最新交易日 %d', mk.n, mk.m, last)
    if last < today and not args.force:
        log.warning('数据未更新到今天(%d)，如需强制运行加 --force', today)

    # ---- 逐策略执行 ----
    ok = fail = 0
    for meta in metas:
        sid, name = meta['id'], meta.get('name', '')
        try:
            ts = time.time()
            trades, metrics = mk.run(meta['dsl'])
            if not args.dry_run:
                repo.save_trades(sid, 'backtest', trades)
                repo.save_metrics(sid, 'backtest', metrics)
            log.info('[%s] %s  笔数=%d 胜率=%.1f%% 平均=%.2f%% (%.1fs)',
                     sid, name, metrics['trades'], metrics['win_rate'] * 100,
                     metrics['avg_return'] * 100, time.time() - ts)
            ok += 1
        except Exception as e:
            log.exception('[%s] %s 执行失败: %s', sid, name, e)
            fail += 1

    log.info('=== 完成: 成功 %d, 失败 %d, 耗时 %.1fs ===',
             ok, fail, time.time() - t0)
    return 0 if fail == 0 else 2


if __name__ == '__main__':
    sys.exit(main())
