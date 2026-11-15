# -*- coding: utf-8 -*-
"""一键自检：环境 / 数据 / LLM / 编译 / 回测 全链路

用法（在服务器上）：
    cd /usr/strategy-racecourse
    .venv/bin/python -m app.selftest
    .venv/bin/python -m app.selftest --limit 300     # 少量股票加速
    .venv/bin/python -m app.selftest --skip-backtest # 只测 LLM

退出码：0 全通过 / 1 有失败项
"""
from __future__ import annotations

import argparse
import os
import platform
import sys
import time

OK, FAIL, WARN = '[通过]', '[失败]', '[警告]'
results: list[tuple[str, str, str]] = []


def rec(status: str, name: str, detail: str = ''):
    results.append((status, name, detail))
    print(f'  {status} {name}' + (f'  {detail}' if detail else ''))


def section(t):
    print(f'\n{"=" * 70}\n{t}\n{"-" * 70}')


# 用于检验编译质量的真实描述
CASES = [
    '箱体位置低于20%且下影线超过2%，止盈15%止损12%，持有20天',
    '股价在箱体内中低位，选箱体位置最低的，不要突破也不要跌破',
    '箱底放量1.5倍买入，止盈10%止损8%',
    '跌破MA20并且缩量，反弹时买入',
    '连续3天下跌后出现长下影线，买最便宜的',
]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=None, help='只加载前 N 只股票')
    ap.add_argument('--skip-backtest', action='store_true')
    a = ap.parse_args(argv)

    t_all = time.time()
    print('策略赛马场 自检')
    print(f'时间 {time.strftime("%Y-%m-%d %H:%M:%S")}  '
          f'Python {platform.python_version()}  {platform.system()}')

    # ---------- 1. 环境 ----------
    section('1. 环境变量')
    vipdoc = os.environ.get('TDX_VIPDOC', '/data/vipdoc')
    data = os.environ.get('RACE_DATA', './data')
    print(f'  TDX_VIPDOC = {vipdoc}')
    print(f'  RACE_DATA  = {data}')
    print(f'  RACE_LIMIT = {os.environ.get("RACE_LIMIT", "(不限)")}')
    rec(OK if os.path.isdir(vipdoc) else FAIL, '数据目录存在', vipdoc)
    if not os.path.isdir(vipdoc):
        return finish()

    try:
        os.makedirs(data, exist_ok=True)
        rec(OK, '存储目录可写', data)
    except Exception as e:
        rec(FAIL, '存储目录可写', str(e))

    # ---------- 2. 数据 ----------
    section('2. 行情数据')
    try:
        from .engine import detect_layout, list_codes, read_day
        base, flat = detect_layout(vipdoc)
        files = list_codes(base, flat)
        rec(OK if files else FAIL, '布局识别',
            ('flat（文件名含反斜杠）' if flat else 'nested（标准目录）') + f' · {base}')
        rec(OK if files else FAIL, '股票数量', f'{len(files)} 只')
        if files:
            arr = read_day(files[0][1])
            rec(OK, '文件可读',
                f'{files[0][0]} {len(arr)} 条 {int(arr["date"][0])}~{int(arr["date"][-1])}')
    except Exception as e:
        rec(FAIL, '行情数据', f'{type(e).__name__}: {e}')
        return finish()

    # ---------- 3. LLM 配置 ----------
    section('3. LLM 配置')
    from . import llm
    cfg = llm.config()
    print(f'  base_url = {cfg["base_url"]}')
    print(f'  model    = {cfg["model"]}')
    print(f'  json_mode= {cfg["json_mode"]}   timeout={cfg["timeout"]}s')
    if cfg['api_key']:
        rec(OK, 'API Key 已配置',
            cfg['api_key'][:4] + '****' + cfg['api_key'][-4:])
    else:
        rec(WARN, 'API Key 未配置', '将只使用规则解析')
        print('\n  提示：设置 LLM_API_KEY 后可用自然语言生成策略')

    # ---------- 4. LLM 连通 ----------
    section('4. LLM 连通性')
    if llm.is_configured():
        r = llm.test_connection()
        if r['ok']:
            rec(OK, '真实调用成功', f'{r["model"]} · {r["latency"]}s')
        else:
            rec(FAIL, '真实调用失败', str(r.get('reason'))[:120])
    else:
        rec(WARN, '跳过', '未配置 key')

    # ---------- 5. 编译用例 ----------
    section('5. 自然语言编译（5 个真实描述）')
    from .compiler import compile_strategy, UnsupportedIndicator
    compiled = []
    for text in CASES:
        try:
            t0 = time.time()
            r = compile_strategy(text)
            dt = time.time() - t0
            n_sig = len(r['dsl'].get('signal', []))
            rec(OK, f'[{r["source"]}] {text[:22]}',
                f'{n_sig} 条件 · {dt:.1f}s')
            print(f'         解释: {r["interpreted_cn"]}')
            print(f'         排序: {r["dsl"].get("rank_by")}')
            compiled.append((text, r))
        except UnsupportedIndicator as e:
            rec(WARN, f'[拒绝] {text[:22]}', str(e.items)[:80])
        except Exception as e:
            rec(FAIL, f'[异常] {text[:22]}', f'{type(e).__name__}: {e}')

    # ---------- 6. 白名单 ----------
    section('6. 白名单校验')
    from .compiler import validate
    bad = validate({"signal": [{"ind": "turnover_rate", "window": 5, "op": ">=", "value": 5}],
                    "rank_by": {"ind": "turnover_rate", "window": 5}})
    rec(OK if bad else FAIL, '非法指标能被拦截', str(bad))
    good = validate(compiled[0][1]['dsl']) if compiled else []
    rec(OK if not good else FAIL, '合法 DSL 能通过', str(good))

    # ---------- 7. 端到端 ----------
    if not a.skip_backtest:
        section('7. 端到端：编译 → 建策略 → 回测')
        try:
            from .engine import Market
            from .store import Repo
            from datetime import datetime

            t0 = time.time()
            mk = Market(vipdoc, limit=a.limit)
            rec(OK, '加载行情矩阵', f'{mk.n} 只 × {mk.m} 天 · {time.time()-t0:.1f}s')
            print(f'         风险过滤可用比例 {mk.risk_mask().mean()*100:.1f}%  '
                  f'(流通股本 {int((mk.float_sh>0).sum())} 只)')

            repo = Repo(data)
            dsl = (compiled[0][1]['dsl'] if compiled else {
                "signal": [{"ind": "box_position", "window": 120, "op": "<=", "value": 0.2}],
                "rank_by": {"ind": "box_position", "window": 120, "desc": False},
                "entry": {"max_per_day": 1},
                "exit": {"take_profit": 0.15, "stop_loss": 0.12, "max_hold_days": 20}})
            t0 = time.time()
            trades, m = mk.run(dsl)
            dt = time.time() - t0
            if m['trades'] > 0:
                rec(OK, '回测出结果',
                    f"{m['trades']} 笔 · 胜率 {m['win_rate']*100:.1f}% · "
                    f"平均 {m['avg_return']*100:+.2f}% · {dt:.1f}s")
            else:
                rec(FAIL, '回测 0 笔', '检查数据日期 / 风险过滤 / 信号条件')

            sid = 's_selftest_' + datetime.now().strftime('%H%M%S')
            repo.save_meta(dict(id=sid, name='自检策略', raw_text='selftest',
                                interpreted_cn='自检用', dsl=dsl,
                                created_at=datetime.now().isoformat(timespec='seconds')))
            repo.save_trades(sid, 'backtest', trades)
            repo.save_metrics(sid, 'backtest', m)
            rec(OK, '存储读写', f'策略 {sid}')
            if repo.list_meta():
                rec(OK, '排行榜可用', f'{len(repo.list_meta())} 个策略')
        except Exception as e:
            import traceback
            traceback.print_exc()
            rec(FAIL, '端到端', f'{type(e).__name__}: {e}')

    print(f'\n总耗时 {time.time()-t_all:.1f}s')
    return finish()


def finish():
    section('汇总')
    n_ok = sum(1 for s, _, _ in results if s == OK)
    n_warn = sum(1 for s, _, _ in results if s == WARN)
    n_fail = sum(1 for s, _, _ in results if s == FAIL)
    print(f'  通过 {n_ok}   警告 {n_warn}   失败 {n_fail}')
    if n_fail:
        print('\n  失败项：')
        for s, name, detail in results:
            if s == FAIL:
                print(f'    - {name}  {detail}')
        print('\n  请把以上完整输出发给开发者')
        return 1
    print('\n  全部通过 ✓')
    return 0


if __name__ == '__main__':
    sys.exit(main())
