# -*- coding: utf-8 -*-
"""风险过滤：上市日期（次新股）、低价股、小市值

数据源: D:\\new_tdx\\T0002\\hq_cache\\base.dbf（dBase 格式）
字段: SC=市场 GPDM=代码 SSDATE=上市日期 LTAG=流通A股(万股)
"""
from __future__ import annotations

import struct
from pathlib import Path


def read_dbf(path: str | Path) -> list[dict]:
    b = Path(path).read_bytes()
    if len(b) < 32:
        return []
    _, _, _, _, nrec, hdrlen, reclen = struct.unpack('<BBBBIHH', b[:12])
    nfield = (hdrlen - 33) // 32
    fields = []
    for i in range(nfield):
        off = 32 + i * 32
        name = b[off:off + 11].split(b'\x00')[0].decode('latin1').strip()
        ln = b[off + 16]
        fields.append((name, ln))
    out = []
    for r in range(nrec):
        s = hdrlen + r * reclen
        rec = b[s:s + reclen]
        if len(rec) < reclen or rec[:1] == b'*':
            continue
        vals, pos = {}, 1
        for name, ln in fields:
            raw = rec[pos:pos + ln]
            pos += ln
            try:
                vals[name] = raw.decode('gbk').strip()
            except Exception:
                vals[name] = ''
        out.append(vals)
    return out


def load_stock_base(path: str | Path) -> dict[str, dict]:
    """返回 {'sh600000': {'list_date': 19991110, 'float_shares': 2935208.0}, ...}"""
    if not Path(path).exists():
        return {}
    res = {}
    for row in read_dbf(path):
        code = row.get('GPDM', '')
        sc = row.get('SC', '')
        if not code:
            continue
        mk = 'sh' if sc == '1' else ('sz' if sc == '0' else '')
        if not mk:
            continue
        try:
            ld = int(row.get('SSDATE') or 0)
        except ValueError:
            ld = 0
        try:
            fs = float(row.get('LTAG') or 0)       # 万股
        except ValueError:
            fs = 0.0
        res[f'{mk}{code}'] = dict(list_date=ld, float_shares=fs)
    return res
