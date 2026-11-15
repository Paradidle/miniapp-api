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
    'close': dict(
        window=False, name='收盘价',
        desc='当日收盘价（元）',
        formula='C[t]',
        params='无需参数',
        range='> 0 的实数',
        example='{"ind":"close","op":"<","value":6} → 股价低于 6 元',
        note='直接用绝对价格。用它排序可选出「最便宜的」股票，但低价股退市风险更高。',
    ),
    'ret': dict(
        window=True, name='N 日涨幅',
        desc='最近 N 个交易日的累计涨跌幅',
        formula='C[t] / C[t-N] - 1',
        params='window = N（回看天数）',
        range='小数，如 -0.15 表示下跌 15%',
        example='{"ind":"ret","window":3,"op":"<","value":0} → 3 日累计下跌',
        note='注意是「累计」不是「连续每天跌」。要表达连续阴线，需多个条件组合，当前 DSL 不支持。',
    ),
    'ma': dict(
        window=True, name='N 日均线',
        desc='最近 N 个交易日收盘价的算术平均',
        formula='mean(C[t-N : t])',
        params='window = N',
        range='正数（元）',
        example='{"ind":"ma","window":20,"op":">","value":10}',
        note='均线本身是绝对价格，跨股票不可比，通常配合 close_to_ma 使用。',
    ),
    'close_to_ma': dict(
        window=True, name='均线偏离',
        desc='收盘价相对 N 日均线的偏离程度',
        formula='C[t] / MA(N) - 1',
        params='window = N',
        range='小数；< 0 表示在均线下方',
        example='{"ind":"close_to_ma","window":20,"op":"<","value":0} → 跌破 MA20',
        note='跨股票可比，是判断「多头/空头排列」最常用的指标。',
    ),
    'vol_ratio': dict(
        window=True, name='量比',
        desc='当日成交量相对前 N 日均量的倍数',
        formula='V[t] / mean(V[t-N : t])',
        params='window = N（均量基准天数，常用 5 或 7）',
        range='≥ 0；1 = 与均量持平，1.5 = 放量 50%，0.7 = 缩量 30%',
        example='{"ind":"vol_ratio","window":7,"op":">=","value":1.5} → 放量 1.5 倍',
        note='分母不含当天，所以「1.5 倍」是相对「昨天往前 N 天」的均值。⚠ 实测：加放量过滤反而降低收益。',
    ),
    'amount_ratio': dict(
        window=True, name='额比',
        desc='当日成交额相对前 N 日均额的倍数',
        formula='A[t] / mean(A[t-N : t])',
        params='window = N',
        range='≥ 0，含义同量比',
        example='{"ind":"amount_ratio","window":5,"op":">=","value":2}',
        note='比量比更能反映真实资金量（成交量受价格影响）。',
    ),
    'high_n': dict(
        window=True, name='N 日最高价',
        desc='过去 N 个交易日（不含今天）的最高价',
        formula='max(H[t-N : t])',
        params='window = N',
        range='正数（元）',
        example='{"ind":"high_n","window":60,"op":"<","value":20}',
        note='**不含当天**是刻意设计，这样当天突破时才有意义。',
    ),
    'low_n': dict(
        window=True, name='N 日最低价',
        desc='过去 N 个交易日（不含今天）的最低价',
        formula='min(L[t-N : t])',
        params='window = N',
        range='正数（元）',
        example='{"ind":"low_n","window":120,"op":">","value":3}',
        note='**不含当天**。',
    ),
    'breakout': dict(
        window=True, name='突破幅度',
        desc='收盘价相对前 N 日最高价的突破幅度',
        formula='C[t] / high_n(N) - 1',
        params='window = N（前高回看天数）',
        range='< 0 未突破；≥ 0 已突破；如 0.02 表示超出前高 2%',
        example='{"ind":"breakout","window":60,"op":">=","value":0} → 突破 60 日新高',
        note='⚠ 实测：突破新高买入 20 日超额 −2.51%（t=−20.5），是全样本最稳定的负信号之一。',
    ),
    'box_position': dict(
        window=True, name='箱体位置',
        desc='收盘价在过去 N 日高低区间中的相对位置',
        formula='(C[t] - low_n) / (high_n - low_n)',
        params='window = N（区间回看天数，常用 120）',
        range='0 = 箱底，1 = 箱顶；**> 1 表示突破上方，< 0 表示跌破下方**',
        example='{"ind":"box_position","window":120,"op":"<=","value":0.2} → 箱体底部 20% 以内',
        note='⚠ 这是「滚动区间位置」，**不是识别出的箱体**：不做跨度、触碰次数、形态校验。'
             '如果你要的是「3 年内找箱体、跨度至少 30 天」那种，当前不支持，'
             '可用更大的 window（如 250/750）近似，但不做形态判定。',
    ),
    'in_box': dict(
        window=True, name='是否在箱体内',
        desc='收盘价是否严格落在过去 N 日高低区间内部',
        formula='1 if low_n < C[t] < high_n else 0',
        params='window = N',
        range='1 = 在箱内；0 = 突破或跌破',
        example='{"ind":"in_box","window":120,"op":"==","value":1} → 只在箱体内选股',
        note='等价于 box_position 落在 (0,1) 开区间。用来明确表达「不要突破也不要跌破」。',
    ),
    'box_amplitude': dict(
        window=True, name='箱体高度',
        desc='过去 N 日最高价与最低价之差，相对最低价的比例',
        formula='(high_n - low_n) / low_n',
        params='window = N',
        range='小数；0.15 表示箱体高度是最低价的 15%',
        example='{"ind":"box_amplitude","window":120,"op":"between","value":[0.06,0.3]}',
        note='用来要求「像箱子」而不是「一路单边」。⚠ 实测：加这个过滤反而降低收益。',
    ),
    'shadow_up': dict(
        window=True, name='上影线',
        desc='上影线长度占收盘价的比例',
        formula='(H[t] - max(O[t], C[t])) / C[t]',
        params='无需 window',
        range='≥ 0；0.03 表示上影线是收盘价的 3%',
        example='{"ind":"shadow_up","op":">=","value":0.03} → 长上影线',
        note='长上影线常表示上方抛压重，是「高位滞涨」的典型特征。',
    ),
    'shadow_down': dict(
        window=True, name='下影线',
        desc='下影线长度占收盘价的比例',
        formula='(min(O[t], C[t]) - L[t]) / C[t]',
        params='无需 window',
        range='≥ 0；0.02 表示下影线是收盘价的 2%',
        example='{"ind":"shadow_down","op":">=","value":0.02} → 下影线超过 2%',
        note='长下影线表示下方有承接。✅ 实测：这是全样本里少数真正有效的信号，'
             '加它能显著提升收益（去掉后期望从 −0.46% 掉到 −1.32%）。',
    ),
    'amplitude': dict(
        window=True, name='振幅',
        desc='过去 N 日最高价与最低价之差，相对当日收盘价的比例',
        formula='(high_n - low_n) / C[t]',
        params='window = N',
        range='小数',
        example='{"ind":"amplitude","window":20,"op":">=","value":0.15}',
        note='和 box_amplitude 类似，分母不同（这个是除以当前价）。',
    ),
    'box_pos_real': dict(
        window=False, name='真箱体位置', params=True,
        desc='识别出的一段横盘区间中，当前价所处的位置',
        formula='(C[t] - 箱底) / (箱顶 - 箱底)',
        params_desc='lookback 观察天数，min_span 最短跨度，min_touches 每边最少触碰',
        range='0 = 箱底，1 = 箱顶；无有效箱体时为空值（该股票当天被排除）',
        example='{"ind":"box_pos_real","params":{"lookback":750,"min_span":60,"min_touches":2},"op":"<=","value":0.3}',
        note='**这是识别出的箱体**：会检查跨度、高度、上下沿触碰次数、是否单边倾斜。'
             '从长到短尝试候选跨度，取最长的那个成立的箱体。'
             '与 box_position 的区别：后者只是滚动高低区间，不做形态判定。',
    ),
    'box_valid': dict(
        window=False, name='是否有箱体', params=True,
        desc='当天是否存在符合参数的箱体',
        formula='1 / 0',
        params_desc='同 box_pos_real',
        range='1 = 有箱体，0 = 无',
        example='{"ind":"box_valid","op":"==","value":1} → 只要有箱体才选',
        note='用来显式剔除「没有箱体形态」的股票。',
    ),
    'box_span_real': dict(
        window=False, name='箱体跨度', params=True,
        desc='识别出的箱体持续了多少个交易日',
        formula='选中的 span',
        params_desc='同 box_pos_real',
        range='≥ min_span 的整数',
        example='{"ind":"box_span_real","params":{"lookback":750,"min_span":60},"op":">=","value":120}',
        note='可以要求「至少 3 个月的箱体」之类。',
    ),
    'box_amp_real': dict(
        window=False, name='真箱体高度', params=True,
        desc='识别出的箱体高度相对箱底的比例',
        formula='(箱顶 - 箱底) / 箱底',
        params_desc='同 box_pos_real',
        range='小数，如 0.15 表示 15%',
        example='{"ind":"box_amp_real","op":"between","value":[0.06,0.2]}',
        note='要求「窄幅箱体」时用这个。',
    ),
    'limit_up': dict(
        window=False, name='是否涨停',
        desc='当日涨幅是否达到 9.8% 以上',
        formula='1 if ret(1) >= 0.098 else 0',
        params='无需参数',
        range='0 或 1',
        example='{"ind":"limit_up","op":"==","value":0} → 排除涨停',
        note='简化判定（统一 9.8%），未区分主板/创业板/ST 的不同涨跌幅限制。',
    ),
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
    seen, uniq = set(), []
    for x in bad:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    bad = uniq
    for c in dsl.get('signal', []):
        if c.get('ind') in INDICATORS and not INDICATORS[c['ind']]['window']:
            c.pop('window', None)
    return bad



_CN_NUM = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6}


def parse_box_params(text: str) -> dict:
    """从中文描述里提取箱体参数。

    「5年内找箱体，跨度至少60天，触碰2次」→ {lookback:1250, min_span:60, min_touches:2}
    """
    t = text.replace(' ', '')

    # 观察窗口
    lookback = 250
    m = re.search(r'(\d+)\s*年', t)
    if m:
        lookback = max(20, int(m.group(1)) * 250)
    elif '半年' in t:
        lookback = 125
    elif '一年' in t:
        lookback = 250
    elif re.search(r'(\d+)\s*个?月', t):
        lookback = max(20, int(re.search(r'(\d+)\s*个?月', t).group(1)) * 21)

    # 最短跨度
    min_span = 30
    m = re.search(r'(?:跨度|持续|至少|不少于|大于)\D{0,6}?(\d+)\s*[天日]', t)
    if m:
        min_span = max(5, int(m.group(1)))
    else:
        m = re.search(r'(\d+)\s*个?月\D{0,4}?[以之]?上', t)
        if m:
            min_span = max(5, int(m.group(1)) * 21)

    # 触碰次数
    min_touches = 2
    m = re.search(r'触[碰]?\D{0,3}?(\d+)\s*次', t)
    if m:
        min_touches = max(1, int(m.group(1)))
    else:
        m = re.search(r'触[碰]?\D{0,3}?([一二两三四五])\s*次', t)
        if m:
            min_touches = _CN_NUM.get(m.group(1), 2)
    if re.search(r'只要?触[碰]?[过一]?次|触一次就', t):
        min_touches = 1

    return dict(lookback=int(lookback), min_span=int(min_span),
                min_touches=int(min_touches))


def wants_real_box(text: str) -> bool:
    """是否需要「真箱体识别」（而不是滚动区间）"""
    if re.search(r'真箱体|识别箱体|找箱体|箱体形态|成箱|形成箱体', text):
        return True
    # 出现「N年」「跨度」「触碰」等参数词，且提到箱体
    if '箱' in text and re.search(r'\d+\s*年|跨度|触[碰]?|\d+\s*[天日].*箱', text):
        return True
    return False


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
    elif not pos_set and ('箱底' in text or '箱体底部' in text):
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

    # 真箱体：把 box_position 条件换成 box_pos_real + params
    if wants_real_box(text):
        bp = parse_box_params(text)
        new_sig, replaced = [], False
        for x in sig:
            if x['ind'] == 'box_position':
                if not replaced:
                    new_sig.append(dict(ind='box_pos_real', params=bp,
                                        op=x.get('op', '<='), value=x.get('value', 0.2)))
                    notes.append(f'真箱体(观察{bp["lookback"]}天/跨度≥{bp["min_span"]}天/'
                                 f'触{bp["min_touches"]}次)')
                    replaced = True
            else:
                new_sig.append(x)
        if not replaced:
            new_sig.append(dict(ind='box_pos_real', params=bp, op='<=', value=0.2))
            notes.append(f'真箱体(观察{bp["lookback"]}天/跨度≥{bp["min_span"]}天/'
                         f'触{bp["min_touches"]}次)')
        sig = new_sig
        box_rank = dict(ind='box_pos_real', params=bp, desc=False)

    if not sig:
        hits = [f'{k}（{v}）' for k, v in UNSUPPORTED_HINTS.items() if k in text.lower()]
        raise UnsupportedIndicator(hits or ['无法从描述中识别出任何支持的指标'])

    # 去重：同一条件只保留一次
    seen_k, uniq = set(), []
    for x in sig:
        k = (x['ind'], x.get('op'), x.get('window'), json.dumps(x.get('value'), sort_keys=True))
        if k not in seen_k:
            seen_k.add(k)
            uniq.append(x)
    sig = uniq

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

    # 排序：默认取第一个信号指标
    rank = dict(ind=sig[0]['ind'], desc=True)
    if 'window' in sig[0]:
        rank['window'] = sig[0]['window']
    # 语义修正：位置类条件（<= / between）意味着"越靠近箱底越好" → 升序
    real_box = next((x for x in sig if x['ind'] == 'box_pos_real'), None)
    if real_box is not None:
        rank = dict(ind='box_pos_real', params=real_box.get('params'), desc=False)
    else:
        pos_cond = next((x for x in sig if x['ind'] == 'box_position'
                         and x.get('op') in ('<=', '<', 'between')), None)
        if pos_cond is not None:
            rank = dict(ind='box_position', window=120, desc=False)
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
        w = '需要 window 参数' if v['window'] else (
            '需要 params 参数' if v.get('params') else '无需参数')
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

关于「箱体」有两种指标，必须区分：
- box_position：滚动区间位置，只要 window（天数），不做形态判定
- box_pos_real / box_valid / box_span_real / box_amp_real：真箱体识别，
  需要 params：{{"lookback":观察天数,"min_span":最短跨度天数,"min_touches":每边最少触碰次数}}

参数换算参考：
- 「1年内」→ lookback 250；「3年」→ 750；「5年」→ 1250
- 「至少30天」→ min_span 30；「至少60天/三个月」→ min_span 60
- 「触碰1次就算」→ min_touches 1；「触碰2次」→ 2
- 用户提到「箱体」但没说参数时，默认 lookback 250, min_span 30, min_touches 2

示例：用户说「5年内找箱体，跨度至少60天，触过2次，位置在底部30%」应输出
{{"ind":"box_pos_real","params":{{"lookback":1250,"min_span":60,"min_touches":2}},
  "op":"<=","value":0.3}}
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
