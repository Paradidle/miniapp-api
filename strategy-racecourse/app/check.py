# -*- coding: utf-8 -*-
"""数据自检 CLI：排查布局、路径、数据完整性

用法:
    python -m app.check /usr/stock-data-processor/new_tdx
    python -m app.check /data/vipdoc --limit 50
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .engine import FLAT_PROBE, detect_layout, flat_pairs, list_codes, read_day


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('path', help='TDX 根目录 或 vipdoc 目录')
    ap.add_argument('--limit', type=int, default=20, help='列出前 N 个文件')
    a = ap.parse_args(argv)

    print(f'检查路径: {a.path}')
    p = Path(a.path)
    print(f'  存在: {p.exists()}')
    if not p.exists():
        return 1

    try:
        base, flat = detect_layout(a.path)
    except FileNotFoundError as e:
        print('\n[失败] 未识别到通达信日线数据')
        print(e)
        return 1

    print(f'  布局: {"flat（文件名含反斜杠）" if flat else "nested（正常目录）"}')
    print(f'  数据目录: {base}')

    files = list_codes(base, flat)
    print(f'  识别股票: {len(files)} 只')
    if not files:
        print('\n[失败] 没有识别到任何股票文件')
        return 1

    print('\n前 5 个:')
    for code, path in files[:5]:
        print(f'  {code:<10} {path.name}')

    # 读一个文件验证格式
    code, path = files[0]
    try:
        arr = read_day(path)
        print(f'\n读取验证 {code}: {len(arr)} 条, '
              f'{int(arr["date"][0])}~{int(arr["date"][-1])}')
    except Exception as e:
        print(f'\n[失败] 读取 {path} 出错: {e}')
        return 1

    # 检查指数
    idx = (base / FLAT_PROBE) if flat else (base / 'sh' / 'lday' / 'sh000001.day')
    print(f'基准指数: {idx.name} 存在={idx.exists()}')
    if idx.exists():
        ia = read_day(idx)
        print(f'  {len(ia)} 条, 最新 {int(ia["date"][-1])}')

    if flat:
        print('\n[提示] 当前是 flat 布局，可执行以下命令转为标准目录结构：')
        print(f'  python -m app.flatten --root {base}')

    print('\n[通过] 数据可用')
    return 0


if __name__ == '__main__':
    sys.exit(main())
