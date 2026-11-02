# -*- coding: utf-8 -*-
"""LLM 客户端：OpenAI 兼容接口（DeepSeek / SiliconFlow / 通义 / OpenAI 均可）

配置（环境变量）：
    LLM_BASE_URL   默认 https://api.deepseek.com
    LLM_API_KEY    必填
    LLM_MODEL      默认 deepseek-chat
    LLM_TIMEOUT    默认 90 秒
    LLM_JSON_MODE  默认 on；某些服务不支持 response_format 时可设 off
"""
from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE


class LLMError(Exception):
    """LLM 调用失败（网络、认证、解析等）"""


class LLMNotConfigured(LLMError):
    pass


def config() -> dict:
    return dict(
        base_url=(os.environ.get('LLM_BASE_URL') or 'https://api.deepseek.com').rstrip('/'),
        api_key=os.environ.get('LLM_API_KEY') or os.environ.get('DEEPSEEK_API_KEY') or '',
        model=os.environ.get('LLM_MODEL') or 'deepseek-chat',
        timeout=float(os.environ.get('LLM_TIMEOUT', 90)),
        json_mode=str(os.environ.get('LLM_JSON_MODE', 'on')).lower() not in ('off', '0', 'false'),
    )


def is_configured() -> bool:
    return bool(config()['api_key'])


def _post(url: str, payload: dict, api_key: str, timeout: float) -> tuple[int, dict | str]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json',
                 'Authorization': f'Bearer {api_key}'},
        method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
            return r.status, json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'ignore')
        return e.code, body
    except Exception as e:
        raise LLMError(f'请求失败: {type(e).__name__}: {e}')


def _extract_json(text: str) -> dict:
    """从回复里抠出 JSON（兼容 ```json 包裹、前后有解释文字的情况）"""
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r'```(?:json)?\s*(.+?)```', text, re.S)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except Exception:
            pass
    i, j = text.find('{'), text.rfind('}')
    if i >= 0 and j > i:
        try:
            return json.loads(text[i:j + 1])
        except Exception:
            pass
    raise LLMError(f'无法从回复中解析出 JSON: {text[:200]}')


def chat_json(messages: list[dict], temperature: float = 0.0) -> dict:
    """调用 LLM 并返回解析后的 JSON。

    返回 {'data': dict, 'raw': str, 'usage': dict, 'latency': float, 'model': str}
    """
    cfg = config()
    if not cfg['api_key']:
        raise LLMNotConfigured('未配置 LLM_API_KEY')

    url = cfg['base_url'] + '/v1/chat/completions'
    if cfg['base_url'].endswith('/v1'):
        url = cfg['base_url'] + '/chat/completions'

    payload = dict(model=cfg['model'], messages=messages, temperature=temperature)
    if cfg['json_mode']:
        payload['response_format'] = {'type': 'json_object'}

    t0 = time.time()
    status, body = _post(url, payload, cfg['api_key'], cfg['timeout'])

    # 某些服务不支持 response_format，去掉重试一次
    if status == 400 and cfg['json_mode']:
        payload.pop('response_format', None)
        status, body = _post(url, payload, cfg['api_key'], cfg['timeout'])

    if status != 200:
        raise LLMError(f'HTTP {status}: {str(body)[:300]}')

    try:
        content = body['choices'][0]['message']['content']
    except Exception:
        raise LLMError(f'响应格式异常: {str(body)[:300]}')

    return dict(
        data=_extract_json(content),
        raw=content,
        usage=body.get('usage', {}),
        latency=round(time.time() - t0, 2),
        model=cfg['model'],
    )


def test_connection() -> dict:
    """健康检查：发一个极小的请求，验证 key / 网络 / 模型是否可用"""
    cfg = config()
    if not cfg['api_key']:
        return dict(ok=False, reason='未配置 LLM_API_KEY',
                    base_url=cfg['base_url'], model=cfg['model'])
    try:
        r = chat_json([
            {'role': 'system', 'content': '只输出 JSON，不要任何解释。'},
            {'role': 'user', 'content': '输出 {"ok": true}'},
        ])
        return dict(ok=True, base_url=cfg['base_url'], model=r['model'],
                    latency=r['latency'], usage=r['usage'])
    except LLMError as e:
        return dict(ok=False, reason=str(e), base_url=cfg['base_url'], model=cfg['model'])
