#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地反代服务器测试脚本（对应 OpencodeZenReverseProxy.py，默认端口 11434）。

覆盖：
  1. GET  /v1/models                 —— 模型列表
  2. POST /v1/chat/completions       —— OpenAI 格式 chat（非流式 + 流式）
  3. POST /v1/messages               —— Anthropic 格式 chat（cc-switch 实际走的通路）

对所有免费模型逐一测 chat，方便确认上游当前哪些可用。

用法：
  python testChat.py                       # 测默认 http://127.0.0.1:11434
  python testChat.py --base http://127.0.0.1:11434
  python testChat.py --model deepseek-v4-flash-free   # 只测指定模型
  python testChat.py --start               # 自动拉起本地反代再测

依赖：requests
"""

import argparse
import json
import subprocess
import sys
import time

import requests

# 默认测试模型（与 cc-switch / 用户常用的一致）
DEFAULT_MODEL = "big-pickle"
UPSTREAM_HEADERS = {"User-Agent": "opencode/1.18.26"}
REQUEST_TIMEOUT = (10, 60)


def log(msg):
    print(msg, flush=True)


def check_reachable(base):
    try:
        r = requests.get(base + "/health", timeout=5)
        return True
    except requests.exceptions.RequestException:
        # /health 不一定实现，再用根路径试探
        try:
            requests.get(base + "/", timeout=5)
            return True
        except requests.exceptions.RequestException:
            return False


def test_models(base):
    log("\n=== 1) GET /v1/models ===")
    try:
        r = requests.get(base + "/v1/models", headers=UPSTREAM_HEADERS, timeout=REQUEST_TIMEOUT)
        log(f"  HTTP {r.status_code}")
        data = r.json()
        models = data.get("data") or []
        ids = [m.get("id") for m in models]
        log(f"  模型数: {len(ids)}")
        log(f"  模型列表: {ids}")
        return ids
    except Exception as e:
        log(f"  ERROR: {type(e).__name__}: {e}")
        return []


def _report(status_code, body_text, label):
    snippet = body_text.replace("\n", " ").strip()
    if len(snippet) > 300:
        snippet = snippet[:300] + " ..."
    log(f"  [{label}] HTTP {status_code}")
    log(f"  body: {snippet}")


def test_chat_nonstream(base, model):
    log(f"\n=== 2a) POST /v1/chat/completions (model={model}, stream=false) ===")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Say hi in one word."}],
        "max_tokens": 20,
        "stream": False,
    }
    try:
        r = requests.post(base + "/v1/chat/completions", headers={"Content-Type": "application/json"},
                          json=payload, timeout=REQUEST_TIMEOUT)
        _report(r.status_code, r.text, "chat")
    except Exception as e:
        log(f"  ERROR: {type(e).__name__}: {e}")


def test_chat_stream(base, model):
    log(f"\n=== 2b) POST /v1/chat/completions (model={model}, stream=true / SSE) ===")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Say hi in one word."}],
        "max_tokens": 20,
        "stream": True,
    }
    try:
        r = requests.post(base + "/v1/chat/completions", headers={"Content-Type": "application/json"},
                          json=payload, timeout=REQUEST_TIMEOUT, stream=True)
        log(f"  HTTP {r.status_code}, content-type: {r.headers.get('Content-Type')}")
        lines = []
        for i, line in enumerate(r.iter_lines(decode_unicode=True)):
            if line:
                lines.append(line)
            if i >= 8:  # 只看前几帧
                break
        for ln in lines:
            log(f"  > {ln[:160]}")
        if not lines:
            log("  (无 SSE 帧返回)")
    except Exception as e:
        log(f"  ERROR: {type(e).__name__}: {e}")


def test_messages(base, model):
    log(f"\n=== 3) POST /v1/messages (Anthropic 格式, model={model}) ===")
    payload = {
        "model": model,
        "max_tokens": 20,
        "messages": [{"role": "user", "content": "Say hi in one word."}],
    }
    try:
        r = requests.post(base + "/v1/messages",
                          headers={"Content-Type": "application/json", "Authorization": "Bearer ublic"},
                          json=payload, timeout=REQUEST_TIMEOUT)
        _report(r.status_code, r.text, "messages")
    except Exception as e:
        log(f"  ERROR: {type(e).__name__}: {e}")


def main():
    parser = argparse.ArgumentParser(description="本地反代服务器测试")
    parser.add_argument("--base", default="http://127.0.0.1:11434", help="反代服务器地址")
    parser.add_argument("--model", default=None, help="指定只测某个模型（否则测所有免费模型）")
    parser.add_argument("--start", action="store_true", help="自动拉起 OpencodeZenReverseProxy.py 再测")
    args = parser.parse_args()

    proc = None
    if args.start:
        import os
        script_dir = os.path.dirname(os.path.abspath(__file__))
        proxy_script = os.path.join(script_dir, "OpencodeZenReverseProxy.py")
        log(f"* 启动本地反代: {proxy_script}")
        proc = subprocess.Popen([sys.executable, proxy_script], cwd=script_dir)
        # 等待端口就绪
        for _ in range(20):
            if check_reachable(args.base):
                break
            time.sleep(0.5)
        else:
            log("! 本地反代未能在预期时间内启动，请手动启动后重试。")
            proc.terminate()
            return

    if not check_reachable(args.base):
        log(f"! 无法连接本地反代 {args.base}")
        log("  请先启动: python OpencodeZenReverseProxy.py")
        log("  或加 --start 自动拉起。")
        if proc:
            proc.terminate()
        return

    models = test_models(args.base)
    targets = [args.model] if args.model else (models or [DEFAULT_MODEL])
    log(f"\n* 将对以下模型执行 chat/messages 测试: {targets}")

    for m in targets:
        test_chat_nonstream(args.base, m)
        test_chat_stream(args.base, m)
        test_messages(args.base, m)

    log("\n=== 测试结束 ===")

    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    main()
