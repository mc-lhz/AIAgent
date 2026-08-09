"""对话客户端（DeepSeek web 端）。

Reverse-engineered from browser DevTools; tested against chat.deepseek.com web build 2.3.0.

本模块只关心「一次对话」的完整链路：PoW 头生成 → 登录校验 → 请求流式 completion → 解析 SSE。
鉴权 / cookie / 设备 ID / 普通只读查询由其他模块负责：
  - deepseekAuthenticate.py  登录原语 + .env 读写 + 失效判定 + 自动重登
  - getChat.py                列出会话 / 会话详情（只读，不带 PoW）
  - getDeviceId.py            数美 smidV2 → device_id 复现
  - solve_wasm_py.py          DeepSeekHashV1 PoW WASM 求解

调用方：`deepseekClient()` 实例化后即可使用 chat / listChats / findChatByTitle / getParentMessageId 等。
"""
import os
import sys

# 确保本文件所在目录在 sys.path 首位，使兄弟模块（solve_wasm_py / getChat /
# deepseekAuthenticate / getDeviceId / constants）无论从哪个工作目录运行本模块都能被导入。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import curl_cffi.requests as requests
import json
import base64

from solve_wasm_py import solve_pow
from getChat import getChatList, getChatInfo
from deepseekAuthenticate import ensureLoggedIn, callWithReLogin, doLogin, isTokenInvalid
from constants import apiBase, impersonate, xClientHeaders

completionPath = "/api/v0/chat/completion"
# 模型停止回答的标志
stopFlag = "FINISHED"


class deepseekClient:
    """DeepSeek 对话客户端（薄门面）：封装 PoW 头生成与 SSE 流解析。

    鉴权（bearer / cookie）、会话定位（chatSessionId / parentMessageId）仍由其他模块
    通过 .env 与 listChats/findChatByTitle/getParentMessageId 提供；本类只持有对话
    本身的算法（PoW + 流式响应解析 + 失效重登）。
    面向调用方：实例化后调用 chat(...) 即可。
    """

    def __init__(self, title="DeepseekHashV1"):
        """实例化即初始化：自动登录获取 token/cookie，并按标题绑定默认会话。

        title: 目标会话标题（需先在 DeepSeek web 端创建同名会话）；
               找不到时 self.chatSessionId 为 None，ask() 会显式报错提示。
        """
        self.bearer, self.cookie = ensureLoggedIn()
        self.title = title
        self.chatSessionId = self.findChatByTitle(title)  # 自动绑定；找不到为 None

    def getPowHeader(self, targetPath, bearer=None, cookie=None, api=apiBase):
        """拉取并解决一次 PoW 挑战，返回 base64 后的 JSON 头（X-DS-PoW-Response 的值）。

        流程：POST /api/v0/chat/create_pow_challenge 带 target_path → 解 WASM DeepSeekHashV1 →
        base64(json.dumps(sol, separators=(',', ':')).encode())。
        关键：target_path 必须与随后要调用的端点路径完全一致（服务端校验挑战与端点绑定）。
        每次调用都重新生成挑战，避免过期。
        """
        if bearer is None:
            bearer = self.bearer
        if cookie is None:
            cookie = self.cookie
        ch = requests.post(f"{api}/api/v0/chat/create_pow_challenge",
                           json={"target_path": targetPath},
                           headers={"Authorization": f"Bearer {bearer}",
                                    "Cookie": cookie,
                                    "Content-Type": "application/json",
                                    "Referer": f"{api}/",
                                    **xClientHeaders},
                           impersonate=impersonate).json()
        sol = solve_pow(ch)          # targetPath 来自挑战本身，天然匹配端点
        return base64.b64encode(json.dumps(sol, separators=(',', ':')).encode()).decode()

    def chat(self, prompt, chatSessionId=None, parentMessageId=None, model="deepseek-chat"):
        """发起一次对话请求（SSE 流式），返回完整文本。

        chatSessionId 缺省回退 self.chatSessionId；parentMessageId 缺省按首条(0)处理。

        自动重登：HTTP 401/403 或 SSE 流内错误帧（走 isTokenInvalid）会触发 doLogin 一次并重试。
        同样遵守「仅重试一次」原则，避免 token 失效 → 重登 → 仍失效的死循环。
        stream=True 是关键：响应体是 SSE，不是单一 JSON。
        prompt 为用户输入；chatSessionId / parentMessageId 由 findChatByTitle + getParentMessageId 提供。
        model 默认 "deepseek-chat"（也可换 "deepseek-coder" 等）。
        """
        chatSessionId = chatSessionId or self.chatSessionId
        if parentMessageId is None:
            parentMessageId = 0

        def _run(bearer, cookie, api=apiBase):
            # PoW 头必须与本端点路径（completionPath）匹配，且每次重新生成避免过期
            # 请求体字段说明：
            #   prompt                用户输入
            #   parent_message_id     0 表示会话首条；否则接上一条 message_id
            #   chat_session_id       会话 ID
            #   referenced_message_ids / ref_file_ids   引用回复 / 引用文件
            #   search                联网搜索开关
            #   think                 深度思考开关
            #   model                 模型名（默认 "deepseek-chat"）
            #   stream                True = SSE 流式；False = 单一 JSON
            powHeader = self.getPowHeader(completionPath, bearer=bearer, cookie=cookie, api=api)
            return requests.post(
                f"{api}{completionPath}",
                json={"prompt": prompt,
                      "parent_message_id": parentMessageId,
                      "chat_session_id": chatSessionId,
                      "referenced_message_ids": [],
                      "ref_file_ids": [],
                      "search": False,
                      "think": False,
                      "model": model,
                      "stream": True},
                headers={"Authorization": f"Bearer {bearer}",
                         "Cookie": cookie,
                         "Content-Type": "application/json",
                         "Referer": f"{api}/",
                         "X-DS-PoW-Response": powHeader,
                         **xClientHeaders},
                impersonate=impersonate,
                stream=True,
            )

        def _stream(resp):
            """从 SSE 响应读取文本，返回 (text, auth_failed)。

            用 iter_content + 手动行缓冲，避免 iter_lines() 在 TCP 分片时丢行/截断
            （curl_cffi/requests 通病，会导致输出头部或中间丢字）。
            """
            text = ""
            auth_failed = False
            buf = b""
            for raw in resp.iter_content(chunk_size=1024):
                if not raw:
                    continue
                buf += raw
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith(b"data:"):
                        line = line[5:].strip()
                    if line == b"[DONE]":
                        break
                    try:
                        j = json.loads(line.decode("utf-8"))
                    except Exception:
                        continue
                    if isTokenInvalid(j):
                        auth_failed = True
                        break
                    thunkText = j.get("v", "")
                    # 首帧：v 是 dict，首个文本藏在 response.fragments[].content（type=="RESPONSE"）
                    if isinstance(thunkText, dict):
                        fragments = thunkText.get("response", {}).get("fragments", [])
                        for frag in fragments:
                            if frag.get("type") == "RESPONSE":
                                piece = frag.get("content", "")
                                if isinstance(piece, str) and piece:
                                    print(piece, end="")
                                    text += piece
                        continue
                    # 增量帧：v 是 str（简单 {"v":...} 与 JSON Patch {"p","o","v":...} 两种）
                    if isinstance(thunkText, str) and thunkText:
                        if thunkText == stopFlag:
                            break
                        print(thunkText, end="")
                        text += thunkText
            return text, auth_failed

        bearer, cookie = self.bearer, self.cookie
        resp = _run(bearer, cookie)
        # HTTP 层鉴权失效（401/403）→ 重登一次（不调用 resp.json()，避免消费流）
        if resp.status_code in (401, 403):
            print("[auth] 检测到 token 失效，重新登录一次…")
            bearer, cookie = doLogin()
            self.bearer, self.cookie = bearer, cookie
            resp = _run(bearer, cookie)
        text, auth_failed = _stream(resp)
        # SSE 流内错误帧也可能表示 token 失效 → 重登一次并重试（仅一次）
        if auth_failed:
            print("[auth] 检测到 token 失效，重新登录一次…")
            bearer, cookie = doLogin()
            self.bearer, self.cookie = bearer, cookie
            resp = _run(bearer, cookie)
            text, _ = _stream(resp)
        return text

    def listChats(self):
        """列出会话列表（lte_cursor.pinned=false），返回 chat_sessions 列表（已解析 dict）。"""
        # 只读 GET，实测无 PoW 也能过（仍带 bearer + cookie 走 callWithReLogin 兜底鉴权）
        resp = callWithReLogin(getChatList, API=apiBase)
        return resp["data"]["biz_data"]["chat_sessions"]

    def findChatByTitle(self, title):
        """按标题查找会话并返回 id（找不到返回 None）。列出时同步打印 id+标题，便于调试。"""
        for session in self.listChats():
            print(session["id"], session["title"])
            if session["title"] == title:
                return session["id"]
        return None

    def getParentMessageId(self, chatId):
        """取会话最后一条消息的 message_id 作为下一条对话的 parent_message_id。

        会话首条对话传 0；之后续写必须接上一条的 message_id，否则服务端会拒或拼接错位。
        无消息 / 字段缺失 / 接口异常时返回 0（按首条处理）。
        """
        info = callWithReLogin(getChatInfo, API=apiBase, chatID=chatId)
        try:
            msgs = info["data"]["biz_data"]["chat_messages"]
            return msgs[-1]["message_id"] if msgs else 0
        except (KeyError, IndexError, TypeError):
            return 0

    def ask(self, prompt, model="deepseek-chat"):
        """对外高层对话函数（外包）：调用方只需传入 prompt，自动续写上下文并返回完整回复文本。

        内部：使用实例化时已绑定的 self.chatSessionId → 取上一条 parent_message_id
        → 调底层 chat(...)。等价于 client.chat(prompt, chatSessionId, parentMessageId)。
        会话未绑定（标题未找到）时显式抛出 RuntimeError 提示先创建。
        """
        if not self.chatSessionId:
            raise RuntimeError(
                f"未绑定会话（标题 {self.title!r} 未找到），请先在 DeepSeek web 端创建同名会话"
            )
        parent = self.getParentMessageId(self.chatSessionId)
        return self.chat(prompt, self.chatSessionId, parentMessageId=parent, model=model)

