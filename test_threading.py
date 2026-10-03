# coding=utf-8
"""测试 ask() 方法的多线程并发安全性"""
import threading
import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from DeepseekChatClient.chatClient import deepseekClient

THREADS = 4
PROMPT = "用一句话回答：什么是多线程？"

results = []
lock = threading.Lock()


def worker(thread_id):
    start = time.time()
    try:
        reply = client.ask(f"{PROMPT}（第{thread_id}次提问）")
        cost = time.time() - start
        with lock:
            results.append({
                "id": thread_id,
                "ok": True,
                "cost": round(cost, 2),
                "reply": reply[:50]
            })
    except Exception as e:
        cost = time.time() - start
        with lock:
            results.append({
                "id": thread_id,
                "ok": False,
                "cost": round(cost, 2),
                "error": f"{type(e).__name__}: {e}"
            })


if __name__ == "__main__":
    # 阶段1：确认客户端初始化（含登录）
    print("[init] 初始化客户端（登录+绑定会话）...")
    client = deepseekClient()
    print(f"[init] 完成，会话ID: {client.chatSessionId}")

    # 阶段2：单次调用确认可用
    print("[single] 单次调用测试...")
    t0 = time.time()
    reply = client.ask("你好，请回复：收到")
    print(f"[single] 完成，耗时{time.time()-t0:.1f}s，回复: {reply[:30]!r}")

    # 阶段3：并发测试
    print(f"[multi] {THREADS}线程并发调用 ask() ...")
    t0 = time.time()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total = time.time() - t0

    print(f"\n===== 结果汇总（总耗时 {total:.1f}s）=====")
    ok_count = 0
    for r in sorted(results, key=lambda x: x["id"]):
        status = "OK" if r["ok"] else "FAIL"
        print(f"线程{r['id']}: {status} 耗时{r['cost']}s")
        if r["ok"]:
            print(f"    回复: {r['reply']}")
        else:
            print(f"    错误: {r['error']}")
        ok_count += 1 if r["ok"] else 0
    print(f"\n成功 {ok_count}/{THREADS}")
