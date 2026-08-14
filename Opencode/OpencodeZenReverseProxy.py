#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zen Proxy MVP — OpenCode Zen 模型列表获取 + 请求转发 (Flask + requests 版)

适配 Windows：
  - 支持双击运行（.bat 启动脚本）
  - 控制台 UTF-8 输出（避免 Windows GBK 编码报错）
  - 绑定 0.0.0.0 方便局域网/虚拟机访问

功能：
   1. GET  /v1/models              -> 从 opencode.ai 拉取模型列表，过滤出免费模型
   2. POST /v1/chat/completions    -> 透传请求到 zen 端点（流式 SSE / JSON）

用法：
   python server.py                          # 默认端口 20128
   PORT=3000 python server.py                 # 自定义端口
   OPENCODE_API_KEY=sk-xxx python server.py   # 配置真实 key

依赖：pip install flask requests
"""

import json
import os
import sys
import time
import uuid

import requests
from flask import Flask, Response, jsonify, request
from ChatProtocol import openAIChatCompletion, anthropicMessages

# ---------- Windows 控制台 UTF-8 适配 ----------
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------- 配置区 ----------

# 端口配置
port = 11434

# 运行时配置
# 上游 base URL
baseAPI = "https://opencode.ai/zen/v1"
#API Key
apiKey = "public"
# 已知免费模型（额外名单；主过滤规则为模型 id 以 -free 结尾）
knownFreeModels = []
# 请求头
userAgent = (
    "opencode/1.18.18"
)
headers = {
    "User-Agent": userAgent,
    "Authorization": f"Bearer {apiKey}",
}



app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False  # 中文不转义


def isFreeModel(modelId):
    """判断模型是否免费：以 -free 结尾 或在已知免费名单"""
    return bool(modelId) and (modelId.endswith("-free") or modelId in knownFreeModels)


def fetchModels():
    """从上游拉取模型列表，过滤免费模型"""
    try:
        response = requests.get(f"{baseAPI}/models", headers=headers, timeout=15)
        response.encoding = "utf-8"
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        raise RuntimeError(f"upstream /models error: {e}")
    raw = data.get("data") or data.get("models") or (data if isinstance(data, list) else [])
    return {"object": "list",
            "data": [{"id": m.get("id") or m.get("name"), "object": "model", "owned_by": "opencode"}
                     for m in raw if isFreeModel(m.get("id") or m.get("name") or "")]}


@app.route("/v1/models", methods=["GET", "OPTIONS"])
def modelsList():
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        return jsonify(fetchModels())
    except Exception as e:
        return jsonify({"error": str(e)}), 502

@app.route("/v1/chat/completions", methods=["POST", "OPTIONS"])
def chatCompletions():
    if request.method == "OPTIONS":
        return ("", 204)
    body = request.get_json(silent=True) or {}
    stream = bool(body.get("stream", False))
    print(f"接收到OpenAI请求：{str(body)[:200]}...，stream={stream}，model={body.get('model', 'unknown')}")
    try:
        result = openAIChatCompletion(headers, body, stream=stream)
    except requests.exceptions.HTTPError as upstreamError:
        # 上游返回错误（4xx/5xx）：原路透传状态码与响应体到下游
        upstreamResponse = upstreamError.response
        return Response(upstreamResponse.content, status=upstreamResponse.status_code,
                        mimetype=upstreamResponse.headers.get("Content-Type", "application/json"))
    except requests.exceptions.RequestException as networkError:
        return jsonify({"error": f"upstream request failed: {networkError}"}), 502
    if stream:
        return Response(result, mimetype="text/event-stream; charset=utf-8")
    return jsonify({
        "id": f"chatcmpl-{uuid.uuid4().hex[:29]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "unknown"),
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": result},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


@app.route("/v1/messages", methods=["POST", "OPTIONS"])
def messages():
    if request.method == "OPTIONS":
        return ("", 204)
    body = request.get_json(silent=True) or {}
    stream = bool(body.get("stream", False))
    print(f"接收到Anthropic请求：{str(body)[:200]}...，stream={stream}，model={body.get('model', 'unknown')}")
    try:
        result = anthropicMessages(headers, body, stream=stream)
    except requests.exceptions.HTTPError as upstreamError:
        # 上游返回错误（4xx/5xx）：原路透传状态码与响应体到下游
        upstreamResponse = upstreamError.response
        return Response(upstreamResponse.content, status=upstreamResponse.status_code,
                        mimetype=upstreamResponse.headers.get("Content-Type", "application/json"))
    except requests.exceptions.RequestException as networkError:
        return jsonify({"error": f"upstream request failed: {networkError}"}), 502
    if stream:
        return Response(result, mimetype="text/event-stream; charset=utf-8")
    else:
        return jsonify({
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": result}],
            "model": body.get("model", "unknown"),
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        })

@app.route("/health", methods=["GET"])
def healthCheck():
    return jsonify({"status": "ok"})



if __name__ == "__main__":
    print("=" * 56)
    print("  Zen Proxy MVP (Flask + requests)")
    print(f"  Listening on http://localhost:{port}")
    print(f"  GET  /v1/models            (upstream: {baseAPI}/models)")
    print(f"  POST /v1/chat/completions  (upstream: {baseAPI}/chat/completions)")
    print(f"  POST /v1/messages        (upstream: {baseAPI}/chat/completions)")
    print(f"  free models -> Bearer public | paid models -> OPENCODE_API_KEY "
          f"{'(configured)' if apiKey else '(NOT set)'}")
    print("=" * 56)
    print("Press Ctrl+C to stop")

    # threaded=True 支持并发请求；host=0.0.0.0 允许局域网访问（Windows 防火墙需放行）
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False)

