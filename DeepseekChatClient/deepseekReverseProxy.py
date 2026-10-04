# coding=utf-8
"""DeepSeek 反向代理 - 兼容 OpenAI Chat Completions 标准（含 Tool Calling 适配）"""
import os
import sys
import uuid
import time
import json
import re
from flask import Flask, request, jsonify, Response

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import Logcat
Log = Logcat.Logcat()
from chatClient import deepseekClient

app = Flask(__name__)
client = None


def getClient():
    global client
    if client is None:
        client = deepseekClient()
    return client


def newId():
    return f'chatcmpl-{uuid.uuid4().hex[:29]}'


def sse(obj):
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# ==================== Tool Calling 适配 ====================
# 模型可能以多种 XML 变体输出工具调用，代理层归一化后转成 OpenAI 标准 tool_calls
invokeClose = '</' + 'invoke' + '>'
paramClose = '</' + 'param' + 'eter>'
invokeRe = re.compile(r'<invoke\s+name="([^"]+)"(.*?)' + invokeClose, re.S)
paramRe = re.compile(r'<parameter\s+name="([^"]+)">(.*?)' + paramClose, re.S)
DSML = '｜｜DSML｜｜'


def normalizeTags(text):
    """把 DSML 风格标签（<｜｜DSML｜｜ invoke / parameter）归一化成普通 XML 标签。"""
    t = text
    t = t.replace('<' + DSML + ' ', '<')
    t = t.replace('</' + DSML + ' ', '</')
    # 去掉外层 <calls> / <tool_calls> 包裹
    t = re.sub(r'</?(?:tool_)?calls>', '', t)
    # 去掉参数标签上的 string="true" 等额外属性，统一成 <parameter name="k">v
    t = re.sub(r'<parameter\s+name="([^"]+)"\s+string="[^"]*">',
               r'<parameter name="\1">', t)
    return t


def parseInvoke(text):
    """多格式解析工具调用，返回 (toolCalls, remainingText)。

    支持 Claude 风格 <invoke name="X"> 与 DSML 风格 <｜｜DSML｜｜ invoke> 变体。
    """
    norm = normalizeTags(text)
    calls = []
    for m in invokeRe.finditer(norm):
        params = {}
        for pm in paramRe.finditer(m.group(2)):
            key = pm.group(1).strip()
            val = pm.group(2).strip()
            try:
                val = json.loads(val)
            except Exception:
                pass
            params[key] = val
        calls.append({
            'id': f'call_{uuid.uuid4().hex[:24]}',
            'type': 'function',
            'function': {'name': m.group(1), 'arguments': json.dumps(params, ensure_ascii=False)}
        })
    parts, pos = [], 0
    for m in invokeRe.finditer(norm):
        parts.append(norm[pos:m.start()])
        pos = m.end()
    parts.append(norm[pos:])
    remaining = '\n'.join(p.strip() for p in parts if p.strip())
    return calls, remaining


def buildPrompt(data):
    """messages + tools → 单段文本 prompt（注入工具定义、调用指令与历史）"""
    lines = []
    for msg in data.get('messages', []):
        role = msg.get('role', 'user')
        content = msg.get('content')
        if isinstance(content, list):
            content = ' '.join(b.get('text', '') for b in content
                               if isinstance(b, dict) and b.get('text'))
        if role == 'assistant' and msg.get('tool_calls'):
            for tc in msg.get('tool_calls', []):
                fn = tc.get('function', {})
                try:
                    args = json.loads(fn.get('arguments', '{}'))
                except Exception:
                    args = {}
                lines.append('assistant: <invoke name="' + str(fn.get('name', '')) + '">')
                for k, v in args.items():
                    v = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                    lines.append('<parameter name="' + str(k) + '">' + str(v) + paramClose)
                lines.append('</invoke>')
            if content:
                lines.append('assistant: ' + content)
            continue
        if role == 'tool':
            lines.append('tool: 工具 ' + str(msg.get('name', '')) + ' 返回了: ' + str(content))
            continue
        if content:
            lines.append(role + ': ' + content)
    tools = data.get('tools')
    if tools:
        lines.append('你可以使用以下工具，需要时只输出 invoke 块，不要输出其他文字:')
        for t in tools:
            fn = t.get('function', t)
            lines.append('- 工具 ' + str(fn.get('name')) + ': ' + str(fn.get('description', ''))
                         + ' 参数: ' + json.dumps(fn.get('parameters', {}), ensure_ascii=False))
    return '\n'.join(lines)


def streamOpenai(prompt, model, tools):
    base = {'id': newId(), 'object': 'chat.completion.chunk',
            'created': int(time.time()), 'model': model}

    def gen():
        yield sse({**base, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})
        full = ''
        try:
            for piece in getClient().askStream(prompt, model=model):
                full += piece
        except Exception as e:
            Log.e('Proxy', str(e))
        if tools:
            Log.d('Proxy', f"模型原始输出: {full!r}")
            calls, remaining = parseInvoke(full)
            if calls:
                delta = {'content': remaining or None,
                         'tool_calls': [{'index': i, 'id': c['id'], 'type': 'function',
                                         'function': {'name': c['function']['name'], 'arguments': ''}}
                          
                                        for i, c in enumerate(calls)]}
                yield sse({**base, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]})
                for i, c in enumerate(calls):
                    yield sse({**base, 'choices': [{'index': 0, 'delta': {
                        'tool_calls': [{'index': i, 'function': {'arguments': c['function']['arguments']}}]
                    }, 'finish_reason': None}]})
                yield sse({**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]})
            else:
                yield sse({**base, 'choices': [{'index': 0, 'delta': {'content': full}, 'finish_reason': None}]})
                yield sse({**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})
        else:
            if full:
                yield sse({**base, 'choices': [{'index': 0, 'delta': {'content': full}, 'finish_reason': None}]})
            yield sse({**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})
        yield "data: [DONE]\n\n"

    return Response(gen(), mimetype='text/event-stream')


@app.route('/v1/chat/completions', methods=['POST'])
def chatCompletions():
    data = request.get_json()
    model = data.get('model', 'deepseek-chat')
    tools = data.get('tools')
    prompt = buildPrompt(data)

    if not prompt:
        return jsonify({'error': {'message': 'No user message found',
                                  'type': 'invalid_request_error'}}), 400

    if data.get('stream'):
        return streamOpenai(prompt, model, tools)

    try:
        text = getClient().ask(prompt, model=model)
        Log.d('Proxy', f"non-stream: {text}")
        message = {'role': 'assistant', 'content': text, 'finish_reason': 'stop'}
        if tools:
            Log.d('Proxy', f"模型原始输出: {text!r}")
            calls, remaining = parseInvoke(text)
            if calls:
                message = {'role': 'assistant',
                           'content': remaining or None,
                           'tool_calls': calls,
                           'finish_reason': 'tool_calls'}
        return jsonify({
            'id': newId(), 'object': 'chat.completion',
            'created': int(time.time()), 'model': model,
            'choices': [{'index': 0, 'message': message}],
            'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
        })
    except Exception as e:
        return jsonify({'error': {'message': str(e), 'type': 'server_error'}}), 500


@app.route('/v1/models', methods=['GET'])
def models():
    return jsonify({'object': 'list',
                    'data': [{'id': 'deepseek-chat', 'object': 'model', 'owned_by': 'deepseek'}]})


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})


if __name__ == '__main__':
    getClient()
    Log.i('Proxy', 'Deepseek Reverse Proxy (OpenAI 兼容 + Tool Calling)')
    Log.i('Proxy', 'POST /v1/chat/completions  (stream=true 走 SSE)')
    app.run(host='0.0.0.0', port=5000, debug=True)
