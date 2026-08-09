#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Python 调用 DeepSeek 真实 PoW WASM 求解 create_pow_challenge 返回的完整 JSON。
用法：
  python solve_wasm_py.py [challenge.json]   # 缺省用内置示例
  cat challenge.json | python solve_wasm_py.py

solve_pow(resp): 接收完整接口返回(dict) -> 返回原始结果 dict（无 base64）。
标准输出为纯 JSON（可 import / 管道）；nonce 与耗时打到 stderr。
"""
import sys, os, json, struct, time
from wasmtime import Store, Module, Linker

WASM = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "sha3_wasm_bg.7b9ca65ddd.wasm")

SAMPLE = {
    "data": {"biz_data": {"challenge": {
        "algorithm": "DeepSeekHashV1",
        "challenge": "bc1018b5fe1c3abc1facd3e21cc547f9caca1aa4c4227d8126b66efcebb430f8",
        "salt": "bf2368e9045422535e86",
        "signature": "5ddab5e3ba5ae4781e5a03d7578b020df187f9a721b9c012d41cd720c4424236",
        "difficulty": 144000, "expire_at": 1786112801999,
        "target_path": "/api/v0/chat/completion"}}}}


def load_wasm():
    """加载 sha3_wasm_bg.7b9ca65ddd.wasm 并返回 (store, exports)。"""
    store = Store()
    inst = Linker(store.engine).instantiate(store, Module.from_file(store.engine, WASM))
    return store, inst.exports(store)


def solve(store, ex, challenge, prefix, difficulty):
    """调用 WASM 的 wasm_solve 在 challenge 中找符合 difficulty 的 nonce；失败返回 None。"""
    mem = ex["memory"]
    w = lambda off, d: mem.write(store, d, off)
    r32 = lambda off: struct.unpack("<i", bytes(mem.read(store, off, off + 4)))[0]
    r64 = lambda off: struct.unpack("<d", bytes(mem.read(store, off, off + 8)))[0]
    try:
        mem.grow(store, 2000)
    except Exception:
        pass
    sp = ex["__wbindgen_add_to_stack_pointer"](store, -16)
    cp = ex["__wbindgen_export_0"](store, len(challenge), 1); w(cp, challenge.encode())
    pp = ex["__wbindgen_export_0"](store, len(prefix), 1);    w(pp, prefix.encode())
    ex["wasm_solve"](store, sp, cp, len(challenge), pp, len(prefix), float(difficulty))
    status, nonce = r32(sp), r64(sp + 8)
    ex["__wbindgen_add_to_stack_pointer"](store, 16)
    return int(nonce) if status else None


def solve_pow(resp):
    """接收完整 create_pow_challenge 返回(dict) -> 返回原始结果 dict（无 base64）。"""
    ch = resp["data"]["biz_data"]["challenge"]
    prefix = f"{ch['salt']}_{ch['expire_at']}_"
    store, ex = load_wasm()
    t0 = time.time()
    nonce = solve(store, ex, ch["challenge"], prefix, ch["difficulty"])
    assert nonce is not None, "WASM 未找到解（prefix/challenge 不匹配）"
    print(f"nonce={nonce}  ({ (time.time()-t0)*1000:.0f} ms)", file=sys.stderr)
    return {
        "algorithm": ch["algorithm"], "challenge": ch["challenge"], "salt": ch["salt"],
        "answer": nonce, "signature": ch["signature"], "target_path": ch["target_path"],
    }


def main():
    """CLI：从 argv[1] / stdin / 内置 SAMPLE 读取 challenge.json，求解并打印 JSON 答案（nonce 耗时走 stderr）。"""
    raw = (open(sys.argv[1]).read() if len(sys.argv) > 1
           else sys.stdin.read() if not sys.stdin.isatty() else None)
    resp = json.loads(raw) if raw else SAMPLE
    result = solve_pow(resp)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
