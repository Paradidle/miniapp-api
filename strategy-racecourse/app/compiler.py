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
    'in_box':         dict(window=True,  desc='收盘价是否在箱体内（1=未突破未跌破）'),
    'box_amplitude':  dict(window=True,  desc='箱体高度/箱底'),
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

    # 箱体位置区间：「中低位」「低位 X%~Y%」
    m = re.search(r'箱体\D{0,4}?(\d+)\s*%?\s*[-~到]\s*(\d+)\s*%', text)
    pos_set = False
    if m:
        lo, hi = pct(m.group(1)), pct(m.group(2))
        sig.append(dict(ind='box_position', window=120, op='between', value=[lo, hi]))
        notes.append(f'箱体位置 {lo*100:.0f}%~{hi*100:.0f}%')
        pos_set = True
    elif re.search(r'中低位|低位区', text):
        sig.append(dict(ind='box_position', window=120, op='between', value=[0.05, 0.40]))
        notes.append('箱体位置 5%~40%（中低位）')
        pos_set = True
    elif re.search(r'箱体低位|低位|箱底', text):
        sig.append(dict(ind='box_position', window=120, op='<=', value=0.2))
        notes.append('箱体位置≤20%')
        pos_set = True

    # 箱体位置（单边阈值，区间已解析则跳过）
    m = None if pos_set else re.search(r'(?:箱体位置|箱底|箱体底部|位置)\D{0,6}?' + _PCT, text)
    if m:
        sig.append(dict(ind='box_position', window=120, op='<=', value=pct(m.group(1))))
        notes.append(f'箱体位置≤{m.group(1)}%')
    elif '箱底' in text or '箱体底部' in text:
        sig.append(dict(ind='box_position', window=120, op='<=', value=0.2))
        notes.append('箱体位置≤20%')

    # 箱体内（未突破未跌破）
    if re.search(r'箱体内|不突破|不跌破|不要突破|箱体之中', text):
        sig.append(dict(ind='in_box', window=120, op='==', value=1))
        notes.append('收盘价在箱体内')

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
    elif '下影线' in text:
        sig.append(dict(ind='shadow_down', op='>=', value=0.02))
        notes.append('下影线≥2%')
    m = re.search(r'上影线\D{0,6}?' + _PCT, text)
    if m:
        sig.append(dict(ind='shadow_up', op='>=', value=pct(m.group(1))))
        notes.append(f'上影线≥{m.group(1)}%')

    # 明确要求"剔除突破/跌破" → 只保留箱体内
    if re.search(r'剔除.{0,4}突破|不要突破|不突破', text):
        if not any(x['ind'] == 'in_box' for x in sig):
            sig.append(dict(ind='in_box', window=120, op='==', value=1))
            notes.append('剔除突破（收盘价在箱体内）')

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

    # 排序：用户说"最便宜的/股价最低"时按 close 升序
    rank = dict(ind=sig[0]['ind'], desc=True)
    if 'window' in sig[0]:
        rank['window'] = sig[0]['window']
    if re.search(r'最便宜|股价最低|价格最低|低价|便宜的', text):
        rank = dict(ind='close', desc=False)
        notes.append('按股价从低到高选')
    elif re.search(r'从低到高|最低位|位置最低|箱底最低|上升排列', text):
        rank = dict(ind='box_position', window=120, desc=False)
        notes.append('按箱体位置从低到高选')
    elif re.search(r'从高到低|最高位|位置最高', text):
        rank = dict(ind='box_position', window=120, desc=True)
        notes.append('按箱体位置从高到低选')

    name = text.strip().replace('\n', ' ')[:12] or '未命名策略'
    return dict(
        name=name,
        interpreted_cn='；'.join(notes) + f'。止盈{tp*100:.0f}% / 止损{sl*100:.0f}% / 持满{hd}日',
        dsl=dict(signal=sig,
                 rank_by=rank,
                 entry=dict(max_per_day=1),
                 exit=dict(take_profit=tp, stop_loss=sl, max_hold_days=hd)),
        unsupported=[],
        source='rule',
    )


# ---------------- 提示词 ----------------
def _indicator_table() -> str:
    lines = []
    for k, v in INDICATORS.items():
        w = '需要 window 参数' if v['window'] else '无需 window'
        lines.append(f'- {k}: {v["desc"]}（{w}）')
    return '\n'.join(lines)


def _system_prompt() -> str:
    return f"""你是量化策略编译器。把用户的中文交易策略翻译成严格的 JSON DSL。

可用指标（只能从这里选，其他一律不支持）：
{_indicator_table()}

输出 JSON 格式：
{{
  "name": "策略名（≤12字）",
  "interpreted_cn": "用一句话复述规则，必须包含所有阈值",
  "dsl": {{
    "signal": [{{"ind":"box_position","window":120,"op":"<=","value":0.2}}],
    "rank_by": {{"ind":"box_position","window":120,"desc":false}},
    "entry": {{"max_per_day": 1}},
    "exit": {{"take_profit":0.15,"stop_loss":0.12,"max_hold_days":20}}
  }},
  "unsupported": []
}}

规则：
1. op 只能是 >= > <= < == != between；between 时 value 写成 [下限, 上限]
2. 百分比一律转小数：5% → 0.05
3. 未指定止盈止损时默认 take_profit=0.08, stop_loss=0.08, max_hold_days=20
4. rank_by.desc=true 表示从大到小选，false 表示从小到大选
   - 用户说"位置最低/从低到高/最便宜/股价最低"时用 desc=false
5. box_position 是 0~1 的位置，>1 表示突破箱体上方，<0 表示跌破箱体下方
   - 用户要求"剔除突破/跌破"时，用 between 限定在 [0,1] 区间内，
     或加 {{"ind":"in_box","op":"==","value":1}}
6. 用户提到换手率/市值/财务/北向/龙虎榜/分钟线等，写进 unsupported 数组，不要编造指标
7. 只输出 JSON，不要任何解释文字
"""


# ---------------- LLM 路径 ----------------
def _llm_compile(text: str, max_repair: int = 1) -> dict:
    """调用 LLM 编译；如果产出了不支持的指标，回喂错误让它修正一次"""
    from . import llm

    messages = [
        {'role': 'system', 'content': _system_prompt()},
        {'role': 'user', 'content': text},
    ]
    r = llm.chat_json(messages)
    out = r['data']
    source = 'llm'

    for _ in range(max_repair):
        dsl = out.get('dsl') or {}
        bad = validate(dsl)
        declared = out.get('unsupported') or []
        problem = sorted(set(bad) | set(declared))
        if not problem:
            break
        messages.append({'role': 'assistant', 'content': r['raw']})
        messages.append({'role': 'user', 'content':
            f'你使用了不可用的指标：{problem}。'
            f'只能使用以下指标：{", ".join(INDICATORS)}。'
            f'请重新输出完整 JSON，不要使用任何其他指标，不要解释。'})
        r = llm.chat_json(messages)
        out = r['data']
        source = 'llm-repaired'

    out['source'] = source
    out['raw'] = r['raw']
    out['latency'] = r['latency']
    out['usage'] = r.get('usage', {})
    return out


# ---------------- 对外接口 ----------------
def compile_strategy(text: str, prefer_llm: bool = True) -> dict:
    """自然语言 → {name, interpreted_cn, dsl, unsupported, source}

    抛 UnsupportedIndicator: 描述里有不支持的指标或无法识别。
    """
    from . import llm

    warnings = []
    result = None

    if prefer_llm and llm.is_configured():
        try:
            result = _llm_compile(text)
        except llm.LLMError as e:
            warnings.append(f'LLM 调用失败，已回退规则解析：{e}')
            result = None

    if result is None:
        result = rule_compile(text)

    # 最终校验：LLM 或规则路径都必须过白名单
    bad = validate(result.get('dsl') or {})
    if bad:
        raise UnsupportedIndicator(sorted(set(bad)))
    if result.get('unsupported'):
        raise UnsupportedIndicator(result['unsupported'])

    if 'dsl' not in result or not (result['dsl'].get('signal')):
        raise UnsupportedIndicator(['无法从描述中识别出任何支持的指标'])

    result.setdefault('name', text.strip()[:12])
    result.setdefault('interpreted_cn', '')
    if warnings:
        result.setdefault('warnings', []).extend(warnings)
    return result
