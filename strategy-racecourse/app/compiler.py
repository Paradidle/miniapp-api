# -*- coding: utf-8 -*-
"""策略编译器：自然语言 → DSL（受限 JSON），不生成可执行代码。

两条路径：
    1. LLM 路径（配置 LLM_API_KEY 时）：调用 OpenAI 兼容接口，要求 JSON 输出
    2. 规则路径（无 key 时兜底）：关键词正则解析，覆盖常见表述

无论哪条路径，输出都经白名单校验；出现不支持的指标直接报错，不进回测。
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

# ---------------- 指标白名单 ----------------
INDICATORS = {
    'close':          dict(window=False, desc='收盘价'),
    'ret':            dict(window=True,  desc='N 日涨幅'),
    'ma':             dict(window=True,  desc='N 日均线'),
    'close_to_ma':    dict(window=True,  desc='收盘价相对 N 日均线的偏离'),
    'vol_ratio':      dict(window=True,  desc='当日成交量 / 前 N 日均量'),
    'amount_ratio':   dict(window=True,  desc='当日成交额 / 前 N 日均额'),
    'high_n':         dict(window=True,  desc='前 N 日最高价'),
    'low_n':          dict(window=True,  desc='前 N 日最低价'),
    'breakout':       dict(window=True,  desc='收盘价相对前 N 日高点的偏离（>0 为突破）'),
    'box_position':   dict(window=True,  desc='箱体位置 0~1（0=箱底, 1=箱顶）'),
    'shadow_up':      dict(window=False, desc='上影线 / 收盘价'),
    'shadow_down':    dict(window=False, desc='下影线 / 收盘价'),
    'amplitude':      dict(window=True,  desc='N 日振幅 / 收盘价'),
    'limit_up':       dict(window=False, desc='是否涨停（0/1）'),
}

UNSUPPORTED_HINTS = {
    '换手率': '缺少流通股本口径的换手率数据',
    '市盈率': '缺少财务数据',
    '市净率': '缺少财务数据',
    'roe': '缺少财务数据',
    '北向': '缺少资金流数据',
    '龙虎榜': '缺少龙虎榜数据',
    '大单': '缺少逐笔成交数据',
    '分钟': '只有日线数据',
    '分时': '只有日线数据',
}

SYSTEM_PROMPT = """你是量化策略编译器。把用户的中文交易策略翻译成严格的 JSON DSL。

可用指标（只能从这里选，其他一律不支持）：
- close: 收盘价
- ret(N): N日涨幅
- ma(N): N日均线
- close_to_ma(N): 收盘价/均线-1
- vol_ratio(N): 当日量/前N日均量
- amount_ratio(N): 当日额/前N日均额
- high_n(N)/low_n(N): 前N日最高/最低价
- breakout(N): 收盘/前N日高点-1（>0 突破）
- box_position(N): 箱体位置 0~1
- shadow_up/shadow_down: 上/下影线占收盘价比例
- amplitude(N): N日振幅
- limit_up: 是否涨停

输出 JSON 格式：
{
  "name": "策略名（≤12字）",
  "interpreted_cn": "把规则复述成一句话，包含所有阈值",
  "dsl": {
    "signal": [{"ind":"box_position","window":120,"op":"<=","value":0.2}],
    "rank_by": {"ind":"vol_ratio","window":7,"desc":true},
    "entry": {"max_per_day": 1},
    "exit": {"take_profit":0.15,"stop_loss":0.12,"max_hold_days":20}
  },
  "unsupported": ["用户提到但无法支持的指标，没有则空数组"]
}

规则：
1. op 只能是 >= > <= < between（between 时 value 为 [低,高]）
2. 百分比转小数：5% → 0.05
3. 用户没提止盈止损时，默认 take_profit=0.08, stop_loss=0.08, max_hold_days=20
4. 用户没提排序时，rank_by 用第一个信号指标的降序
5. 不要编造指标；推理不了的写进 unsupported
"""


class UnsupportedIndicator(Exception):
    def __init__(self, items):
        self.items = items
        super().__init__('不支持的指标: ' + ', '.join(items))


# ---------------- 校验 ----------------
def validate(dsl: dict) -> list[str]:
    """返回不支持的指标列表（空表示通过）"""
    bad = []
    for c in dsl.get('signal', []):
        ind = c.get('ind')
        if ind not in INDICATORS:
            bad.append(str(ind))
        elif INDICATORS[ind]['window'] and 'window' not in c:
            c['window'] = 1
    rb = dsl.get('rank_by') or {}
    if rb.get('ind') and rb['ind'] not in INDICATORS:
        bad.append(str(rb['ind']))
    for c in dsl.get('signal', []):
        if c.get('ind') in INDICATORS and not INDICATORS[c['ind']]['window']:
            c.pop('window', None)
    return bad


# ---------------- 规则兜底 ----------------
_PCT = r'(\d+(?:\.\d+)?)\s*%'
_NUM = r'(\d+(?:\.\d+)?)'


def rule_compile(text: str) -> dict:
    t = text.lower()
    sig, notes = [], []

    def pct(m, d=1.0):
        return round(float(m) / 100.0, 4) if m else d

    # 箱体位置
    m = re.search(r'(?:箱体位置|箱底|箱体底部|位置)\D{0,6}?' + _PCT, text)
    if m:
        sig.append(dict(ind='box_position', window=120, op='<=', value=pct(m.group(1))))
        notes.append(f'箱体位置≤{m.group(1)}%')
    elif '箱底' in text or '箱体底部' in text:
        sig.append(dict(ind='box_position', window=120, op='<=', value=0.2))
        notes.append('箱体位置≤20%')

    # 放量 / 缩量
    m = re.search(r'放量\s*' + _NUM + r'\s*倍', text) or re.search(r'量比\D{0,4}?' + _NUM, text)
    if m:
        sig.append(dict(ind='vol_ratio', window=7, op='>=', value=float(m.group(1))))
        notes.append(f'量比≥{m.group(1)}')
    elif '放量' in text:
        sig.append(dict(ind='vol_ratio', window=7, op='>=', value=1.5))
        notes.append('量比≥1.5')
    elif '缩量' in text:
        v = 0.7
        m2 = re.search(r'缩量\D{0,6}?' + _PCT, text)
        if m2:
            v = round(1 - float(m2.group(1)) / 100.0, 4)
        sig.append(dict(ind='vol_ratio', window=7, op='<=', value=v))
        notes.append(f'量比≤{v}')

    # 影线
    m = re.search(r'下影线\D{0,6}?' + _PCT, text)
    if m:
        sig.append(dict(ind='shadow_down', op='>=', value=pct(m.group(1))))
        notes.append(f'下影线≥{m.group(1)}%')
    m = re.search(r'上影线\D{0,6}?' + _PCT, text)
    if m:
        sig.append(dict(ind='shadow_up', op='>=', value=pct(m.group(1))))
        notes.append(f'上影线≥{m.group(1)}%')

    # 突破 N 日新高
    m = re.search(r'突破\s*' + _NUM + r'\s*日\s*新高', text) or re.search(r'(\d+)\s*日新高', text)
    if m:
        sig.append(dict(ind='breakout', window=int(float(m.group(1))), op='>=', value=0.0))
        notes.append(f'突破{m.group(1)}日新高')

    # 均线
    m = re.search(r'(?:跌破|低于)\s*ma\s*' + _NUM, t) or re.search(r'(?:跌破|低于)\s*' + _NUM + r'\s*日线', text)
    if m:
        sig.append(dict(ind='close_to_ma', window=int(float(m.group(1))), op='<', value=0))
        notes.append(f'收盘低于{m.group(1)}日均线')
    elif re.search(r'(?:站上|高于)\s*ma\s*' + _NUM, t):
        m2 = re.search(r'(?:站上|高于)\s*ma\s*' + _NUM, t)
        sig.append(dict(ind='close_to_ma', window=int(float(m2.group(1))), op='>', value=0))
        notes.append(f'收盘高于{m2.group(1)}日均线')

    # 涨跌幅区间
    m = re.search(r'连续\s*(\d+)\s*天\D{0,8}?' + _PCT + r'\s*[-~到]{1,2}\s*' + _PCT, text)
    if m:
        lo, hi = pct(m.group(2), 0), pct(m.group(3), 0.03)
        sig.append(dict(ind='ret', window=1, op='between', value=[lo, hi]))
        notes.append(f'当日涨幅 {lo*100:.0f}%~{hi*100:.0f}%')

    if not sig:
        hits = [f'{k}（{v}）' for k, v in UNSUPPORTED_HINTS.items() if k in text.lower()]
        raise UnsupportedIndicator(hits or ['无法从描述中识别出任何支持的指标'])

    # 出场
    tp = 0.08
    m = re.search(r'止盈\D{0,4}?' + _PCT, text)
    if m:
        tp = pct(m.group(1))
    sl = 0.08
    m = re.search(r'止损\D{0,4}?' + _PCT, text)
    if m:
        sl = pct(m.group(1))
    hd = 20
    m = re.search(r'持有\D{0,4}?' + _NUM + r'\s*[天日]', text)
    if m:
        hd = int(float(m.group(1)))

    name = text.strip().replace('\n', ' ')[:12] or '未命名策略'
    return dict(
        name=name,
        interpreted_cn='；'.join(notes) + f'。止盈{tp*100:.0f}% / 止损{sl*100:.0f}% / 持满{hd}日',
        dsl=dict(signal=sig,
                 rank_by=dict(ind=sig[0]['ind'], **({'window': sig[0]['window']} if 'window' in sig[0] else {}), desc=True),
                 entry=dict(max_per_day=1),
                 exit=dict(take_profit=tp, stop_loss=sl, max_hold_days=hd)),
        unsupported=[],
        source='rule',
    )


# ---------------- LLM 路径 ----------------
def llm_compile(text: str) -> dict:
    key = os.environ.get('LLM_API_KEY') or os.environ.get('DEEPSEEK_API_KEY')
    base = os.environ.get('LLM_BASE_URL', 'https://api.deepseek.com')
    model = os.environ.get('LLM_MODEL', 'deepseek-flash')
    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': text},
        ],
        'temperature': 0,
        'response_format': {'type': 'json_object'},
    }
    req = urllib.request.Request(
        base.rstrip('/') + '/chat/completions',
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json',
                 'Authorization': f'Bearer {key}'},
        method='POST')
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode('utf-8'))
    content = data['choices'][0]['message']['content']
    out = json.loads(content)
    out['source'] = 'llm'
    return out


# ---------------- 对外接口 ----------------
def compile_strategy(text: str, prefer_llm: bool = True) -> dict:
    """自然语言 → {name, interpreted_cn, dsl, unsupported, source}

    抛 UnsupportedIndicator: 描述里有不支持的指标或无法识别。
    """
    has_key = bool(os.environ.get('LLM_API_KEY') or os.environ.get('DEEPSEEK_API_KEY'))
    result = None
    if prefer_llm and has_key:
        try:
            result = llm_compile(text)
        except Exception as e:
            result = None
            err = str(e)
        if result is None:
            result = rule_compile(text)
            result['warnings'] = [f'LLM 调用失败，已回退规则解析: {err}']
    else:
        result = rule_compile(text)

    bad = validate(result['dsl'])
    if bad:
        raise UnsupportedIndicator(sorted(set(bad)))
    # 用户主动声明了不支持的指标
    if result.get('unsupported'):
        raise UnsupportedIndicator(result['unsupported'])
    return result
