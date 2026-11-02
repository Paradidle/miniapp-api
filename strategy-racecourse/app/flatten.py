# -*- coding: utf-8 -*-
"""把 flat 布局（文件名含反斜杠）迁移成标准目录结构

背景：从 Windows zip 解压到 Linux 时，若解压工具未转换分隔符，
会得到名为 'sh\\lday\\sh600000.day' 的扁平文件，而非 sh/lday/ 目录。

用法:
    python -m app.flatten --root /usr/stock-data-processor/new_tdx --dry-run
    python -m app.flatten --root /usr/stock-data-processor/new_tdx
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .engine import detect_layout


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True, help='数据目录')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)

    try:
        base, flat = detect_layout(a.root)
    except FileNotFoundError as e:
        print(e)
        return 1
    if not flat:
        print(f'{base} 已是标准目录结构，无需迁移')
        return 0

    print(f'数据目录: {base}（flat 布局）')
    moved = skipped = failed = 0
    for f in sorted(base.iterdir(), key=lambda x: x.name):
        name = f.name
        if '\\' not in name or not name.endswith('.day'):
            continue
        parts = name.split('\\')
        if len(parts) != 3 or parts[1] not in ('lday', 'fzline', 'minline', 'eday'):
            continue
        dst = base / parts[0] / parts[1] / parts[2]
        if dst.exists():
            skipped += 1
            continue
        if a.dry_run:
            print(f'  [dry] {name}  ->  {parts[0]}/{parts[1]}/{parts[2]}')
            moved += 1
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dst))
            moved += 1
            if moved % 500 == 0:
                print(f'  ... 已迁移 {moved}')
        except Exception as e:
            failed += 1
            print(f'  [失败] {name}: {e}')
            if failed > 10:
                print('失败过多，中止')
                break

    print(f'\n{"[dry-run] " if a.dry_run else ""}迁移 {moved}，跳过（目标已存在）{skipped}，失败 {failed}')
    if not a.dry_run and moved:
        print('完成，可用 python -m app.check 再次验证')
    return 0 if failed == 0 else 2


if __name__ == '__main__':
    sys.exit(main())
