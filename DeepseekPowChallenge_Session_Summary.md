# DeepSeek PoW Challenge 逆向 —— 本次会话完整总结

> 整理人：WorkBuddy（M3 / mini）
> 会话窗口：2026-08-07 21:50 → 2026-08-09 01:42 (GMT+8)
> 仓库：https://github.com/mc-lhz/DeepseekPowChallenge
> 原始 daily log：`.workbuddy/memory/2026-08-09.md`（按时间追加）

---

## 一、项目背景

对 **chat.deepseek.com**（web build 2.3.0）前端 API 的完整逆向工程：登录鉴权、PoW 求解、SSE 流式对话。

**目标**：纯 Python 复现登录 + 对话能力，无浏览器依赖。
**交付**：可独立运行的客户端（`startChat.py` 入口）+ 公开 GitHub 仓库。

---

## 二、时间线（按对话顺序）

### Phase 1 · 初探与登录
1. **`fetch_page` 实测**：GET `/api/v0/chat_session/fetch_page?lte_cursor.pinned=false` 200，确认只读 GET 无需 PoW。
2. **登录接口分析**：还原 `main_current.js` 的密码登录流程，确认真实端点 `/api/v0/users/login`（不在访客 PoW 白名单，无需 WASM）。
3. **`device_id` 来源**：数美 Shumei SDK `getDeviceId()` 返回 `"B" + smidV2`；`smidV2` 公式：`14位本地时间戳 + md5(uuid_v4) + "00" + md5("smsk_web_"+前段)[:14] + "0"` = 63 字符。
4. **Python 复现**：写 `genSmIdV2()` + `getDeviceId()`。
5. **登录实测**：随机密码 → 422/200 区分 device_id 缺失 vs 值空；填入真实账号 `***REDACTED_PHONE***` / `***REDACTED_PWD***` → 登录成功，`biz_code:0`，token 在 `data.biz_data.user.token`（裸串）。

### Phase 2 · 重构与封装
6. **拆分为**：
   - `loginAPI.py` —— 密码登录原语 `passwordLogin` + 测试台
   - `getDeviceId.py` —— `smidFromCookie` / `genSmIdV2` / `getDeviceId`（小驼峰）
   - 删 `testLogin.py`
7. **抽 `auth.py`**：`.env` 读写、`extractCookies`、`doLogin`、`isTokenInvalid`、`ensureLoggedIn`、`callWithReLogin`。
8. **修 `getChatList.py` 空 token 40003**：默认参数 `BEARER=""` 坑，补真实常量。
9. **cookie 机制**：`.env` 存 BEARER + COOKIE；token 失效自动重登一次。

### Phase 3 · 一致性整改
10. **`constants.py` 单一信息源**（小驼峰 `apiBase`/`impersonate`/`xClientHeaders`），删除重复定义。
11. **`auth.py` + `loginAPI.py` → `deepseekAuthenticate.py`**：合并避免重复实现。
12. **审计报告** `consistency_audit.md`：列出 9 项不一致（X_CLIENT_HEADERS 重复、返回值类型混用、`chat()` 重登游离、凭证硬编码等）。

### Phase 4 · SSE 截断修复
13. **发现**：纯文本"模型是指..."完整，但 JSON 开头 `{"` 丢失。
14. **dump 原始字节流**（临时脚本 `debugStream.py`）：定位首帧 `v` 是 **dict**（`v.response.fragments[].content`），旧逻辑只认 `isinstance(v, str)` 跳过 → 丢 `{"`。
15. **修复 `_stream`**：增加 dict 分支提取 `fragments[].content`；改用 `iter_content` + 手动行缓冲（替代 `iter_lines()`，避免 TCP 分片丢行）。
16. **验证**：实跑返回 text 以 `{"` 开头完整。

### Phase 5 · 类化与实例化
17. **`testCreatePow.py` → `deepseekClient` 类**：把 `__main__` 散落逻辑抽象为方法（`listChats`/`findChatByTitle`/`getParentMessageId`），全小驼峰。
18. **`_stream` 收进 `chat` 内部嵌套函数**（与 `_run` 并列）。
19. **入口外移**：删 `run` 方法 + `__main__`；新建 `startChat.py`（演示 `deepseekClient().chat(...)` 实例化用法）。
20. **文件重命名**：用户把 `testCreatePow.py` → `chatClient.py`，`startChat.py` 同步改 import。

### Phase 6 · 凭证迁移 + GitHub Push
21. **凭证迁移**：MOBILE/EMAIL/PASSWORD/AREA_CODE 从源码迁到 `.env`；`deepseekAuthenticate.py` 新增 `loadCredentials()`，`saveEnv` 改保留其他键，`doLogin`/`__main__` 改读 `.env`。
22. **`.gitignore`**：`.env` / `__pycache__/` / `*.pyc` / `_verify_in.txt` / `debugStream.py` / `verifyFix.py`。
23. **GitHub 推送**：
    - 仓库：https://github.com/mc-lhz/DeepseekPowChallenge
    - 身份：mc-lhz / mc-lhz@users.noreply.github.com（仓库级）
    - 踩坑：Windows schannel `CRYPT_E_NO_REVOCATION_CHECK`，全局 `http.sslBackend schannel` + `http.schannelCheckRevoke false` 解决
    - root commit `39ed3f5`（56 files / 15428 insertions）

### Phase 7 · 注释增补 Push
24. **为项目多写注释**：6 核心模块增补 docstring + inline 注释，不改任何可执行代码。
25. **自检**：用 Python 脚本过滤 `git diff` 中非注释/非 docstring 的代码行变更 = **0 行**。
26. **commit `3f74ce6`**（6 files / +98 / -32），push 成功。

---

## 三、最终项目架构

```
ds_analysis/
├── .env                   # BEARER / COOKIE / MOBILE / EMAIL / PASSWORD / AREA_CODE  ← gitignore
├── .gitignore
├── constants.py           # apiBase / impersonate / xClientHeaders（小驼峰，共享常量）
├── chatClient.py          # deepseekClient 类（chat / listChats / findChatByTitle / getParentMessageId / getPowHeader + 嵌套 _run/_stream）
├── deepseekAuthenticate.py   # passwordLogin / doLogin / isTokenInvalid / ensureLoggedIn / callWithReLogin / loadCredentials
├── getChat.py             # getChatList / getChatInfo / getParentMessageId（只读，无 PoW）
├── getDeviceId.py         # smidFromCookie / genSmIdV2 / getDeviceId（数美 smidV2 复现）
├── solve_wasm_py.py       # DeepSeekHashV1 PoW WASM 求解（依赖 wasmtime + sha3_wasm_bg.7b9ca65ddd.wasm）
├── startChat.py           # 入口示例：deepseekClient() 实例化 + 循环对话
├── consistency_audit.md   # 一致性审计报告（已合并项已标）
└── Analysis/              # 逆向素材（main_current.js / fp-1.min.js / *.wat / 探索脚本），全提交
```

**依赖**：仅 `curl_cffi`（JA3/TLS 模拟，0.16.0）+ `wasmtime`（PoW 求解）。
**Python**：仅系统级 `D:\Program Files\Python\python.exe` 3.11.7 可跑（managed 3.13.12 未装包）。

---

## 四、关键文件说明

### `chatClient.py` —— `deepseekClient` 类
| 方法 | 职责 |
|------|------|
| `getPowHeader(targetPath, bearer, cookie, api)` | POST `/api/v0/chat/create_pow_challenge` → 解 WASM → base64 头 |
| `chat(prompt, chatSessionId, parentMessageId, model)` | POST `/api/v0/chat/completion` SSE 流式；HTTP 401/403 + SSE 错误帧**双层重登** |
| `_stream`（嵌套函数） | iter_content + 手动行缓冲；处理首帧 dict（`v.response.fragments[].content`）+ 后续 v=str 增量 |
| `listChats()` | 列出 chat_sessions（走 `callWithReLogin` 兜底鉴权） |
| `findChatByTitle(title)` | 按标题查会话 id（找不到 None，列出时同步打印） |
| `getParentMessageId(chatId)` | 取最后一条 message_id（首条返 0） |

### `deepseekAuthenticate.py`
| 函数 | 职责 |
|------|------|
| `passwordLogin(API, BEARER, COOKIE, email, password, mobile, areaCode, deviceId)` | POST `/api/v0/users/login`。device_id 字段必填值可空；不在 PoW 白名单 |
| `doLogin()` | 读 .env 凭证 → `passwordLogin` → `extractCookies` 合并 cookie → `saveEnv` 写回 → 返回 (bearer, cookie) |
| `isTokenInvalid(resp)` | HTTP 401/403、code 40003、biz_code 40003/40001、msg 含 "authorization failed"/"invalid token" |
| `callWithReLogin(apiFunc, *args, **kwargs)` | 注入 bearer+cookie；失效重登一次（避免死循环） |
| `loadCredentials()` | 从 .env 读 MOBILE/EMAIL/PASSWORD/AREA_CODE |
| `loadEnv()` / `saveEnv(bearer, cookie)` | .env 读写；`saveEnv` 保留其他键避免覆盖凭证 |

### `getChat.py` —— 只读会话查询（无 PoW）
- `getChatList(API, BEARER, COOKIE)`：GET `/api/v0/chat_session/fetch_page?lte_cursor.pinned=false`
- `getChatInfo(API, BEARER, COOKIE, chatID)`：GET `/api/v0/chat/history_messages?chat_session_id=<uuid>`
- `getParentMessageId(API, BEARER, COOKIE, chatID)`：取最后一条 message_id（首条返 None）

### `getDeviceId.py` —— 数美 smidV2
- `smidFromCookie(cookie)` → regex `smidV2=([0-9a-f]+)`
- `genSmIdV2()` → 14位时间戳 + md5(uuid_v4) + "00" + md5("smsk_web_"+前段)[:14] + "0"，63 字符
- `getDeviceId(cookie)` → 优先用 cookie 里的 smidV2 拼 "B" 前缀；没有则现生成

### `solve_wasm_py.py` —— DeepSeekHashV1 PoW
- `load_wasm()`：加载 `sha3_wasm_bg.7b9ca65ddd.wasm`（wasmtime + Store/Linker）
- `solve(store, ex, challenge, prefix, difficulty)`：调 `wasm_solve` 算 nonce
- `solve_pow(resp)`：从 create_pow_challenge 响应解 → 返回原始结果 dict（不含 base64）

### `constants.py` —— 共享常量
- `apiBase = "https://chat.deepseek.com"`
- `impersonate = "chrome120"`（curl_cffi 浏览器指纹模拟，绕过 HWWAF TLS/JA3 门禁）
- `xClientHeaders`：14 个 web 端遥测头（accept-language / sec-ch-ua / x-client-* 等）

### `startChat.py` —— 入口示例
```python
def startChat(title="DeepseekHashV1"):
    client = deepseekClient()
    # ... 登录 → findChatByTitle → while True: getParentMessageId + chat
```
默认会话 `DeepseekHashV1` 是分析/调试用的固定会话（需先在 web 端手动创建同名会话）。

---

## 五、关键技术发现

### 1. HWWAF + AWS WAF 双层门禁
- **HWWAF**：TLS/JA3 门禁，429。→ curl_cffi `impersonate="chrome120"` 绕过。
- **AWS WAF**：页面内验证码 silent challenge。带旧 HWWAF cookie 直接过（不强制验证）。

### 2. 密码登录端点 `POST /api/v0/users/login`
- **不在访客 PoW 白名单**（无需 `X-DS-Guest-PoW-Response`、无需 WASM）。
- `withToken:false` → 不带 Authorization。
- **字段强校验**：`os="web"`、`email` 或 `mobile`(附 `area_code`)、`password`、**`device_id` 字段必填（缺失 422），值可空**（"" 也能登录）。
- token 在 `data.biz_data.user.token`（**裸字符串**，前端包 `{value, __version:"0"}` 存 localStorage）。

### 3. 数美 Shumei smidV2 指纹
- `14位本地时间戳 + md5(uuid_v4) + "00" + md5("smsk_web_"+前段)[:14] + "0"` = 63 字符
- DeepSeek 登录 `device_id = "B" + smidV2`
- 熵源 `str(uuid.uuid4())` 等价 Shumei RFC-4122 v4 UUID polyfill

### 4. PoW WASM DeepSeekHashV1
- 端点：`POST /api/v0/chat/create_pow_challenge`（body `{"target_path": "..."}`）
- 字段：`algorithm/challenge/salt/signature/difficulty/expire_at/target_path`
- 求解后 `X-DS-PoW-Response = base64(json.dumps(sol, separators=(',', ':')).encode())`
- **关键**：`target_path` 必须与随后调用端点路径完全一致（服务端校验挑战与端点绑定）

### 5. SSE 协议（关键发现）
- **首帧**：`data: {"v": {"response": {... "fragments": [{"type": "RESPONSE", "content": "{\"", ...}]}}}`
  → `v` 是 **dict**，第一个文本在 `v.response.fragments[].content`
- **后续增量帧**：`data: {"v": "response"}` / `data: {"v": "\":"}` / `data: {"v": "我已"}`
  → `v` 是 **str**
- 还可能有 JSON Patch 帧：`data: {"p": "response/fragments/-1/content", "o": "APPEND", "v": "..."}`
  → 增量文本 append
- 终止：内容帧 `v == "FINISHED"` 触发 break
- 实测单次响应耗时 ~80–180 ms（取决于 difficulty）

### 6. cookie 三件套
- 登录实际下发：`HWWAFSESID`、`HWWAFSESTIME`（仅这两个，未下发 `ds_session_id`）
- 数美 `smidV2`：每次请求携带（登录兜底也用）
- HWWAF 首次过：必须带 cookie；无则用现场生成的 `smidV2=<genSmIdV2()>` 兜底

### 7. token 失效判定
- HTTP 401 / 403
- body 顶层 `code == 40003`
- `biz_code in (40003, 40001)`
- SSE 错误帧 `msg` 含 `"Authorization Failed"` / `"invalid token"`（不区分大小写）

---

## 六、安全与部署

### 凭证
- **全部存于 `.env`**（gitignore）：`BEARER`、`COOKIE`、`MOBILE`、`EMAIL`、`PASSWORD`、`AREA_CODE`
- 源码 `grep ***REDACTED_PHONE***|***REDACTED_PWD***` = **No matches**（凭证已全部清除）

### Git
- `.gitignore` 排除：`.env`、`__pycache__/`、`*.pyc`、`*.pyo`、`_verify_in.txt`、`debugStream.py`、`verifyFix.py`
- 仓库级身份：`user.name=mc-lhz`、`user.email=mc-lhz@users.noreply.github.com`（GitHub 隐私邮箱）
- 全局 TLS：`http.sslBackend schannel` + `http.schannelCheckRevoke false`（解决受限网络 CRL 阻塞）

### GitHub 状态
- **仓库**：https://github.com/mc-lhz/DeepseekPowChallenge
- **分支**：`main`
- **Commits**：
  - `39ed3f5` —— root commit（56 files / 15428 insertions）
  - `3f74ce6` —— docs commit（6 files / +98 / -32 注释增补）

---

## 七、临时文件（本地保留，仓库无）

| 文件 | 用途 | 状态 |
|------|------|------|
| `debugStream.py` | SSE 原始字节流 dump（首帧丢失定位） | gitignore，本地保留 |
| `verifyFix.py` | SSE 首帧修复验证 | gitignore，本地保留 |
| `_verify_in.txt` | `startChat.py` 实跑输入 | gitignore，本地保留 |
| `__pycache__/*.pyc` | Python 缓存 | gitignore |

---

## 八、可继续的方向（未做）

1. **HTTP 服务化**：把 `deepseekClient` 用 FastAPI 包成 `POST /chat`，外部应用走 HTTP/SSE 调用。
2. **`deepseekClient` 厚封装**：当前实例无状态（bearer/cookie/chatId 不存为 self）。可改成"实例化后即用"形态（缓存登录态、绑定默认会话），调用方 `client.chat(prompt)` 即可。
3. **凭证管理**：`.env` 是临时方案；建议用 keyring / OS keychain。
4. **PoW 性能**：`solve_pow` 单次 ~80–180 ms；可考虑并行池或预热。
5. **`Analysis/` 子目录**：当前全提交（含 302KB `fp-1.min.js`）；如需精简可分仓。
6. **CI / 测试**：当前无单元测试；可补 `tests/` 用 pytest + 模拟 curl_cffi。
7. **`startChat.py` 是交互式**（`input()` 阻塞）；外部调用走 `client.chat(prompt, ...)` 同步返回。

---

## 九、用户偏好与约定（迁移参考）

- **小驼峰命名**贯穿全项目（类名用户也接受小驼峰 `deepseekClient`；用户偏好"都使用小驼峰"）。
- **函数/方法/变量全小驼峰**；模块常量小驼峰（`apiBase`/`impersonate`/`xClientHeaders`/`completionPath`/`stopFlag`）。
- **类化倾向**：类要"实例化对象后即用服务"，不要把入口逻辑（`run`）写在类里；入口放独立 `*.py`（如 `startChat.py`）。
- **重构底线**："不要改得面目全非"——只加类壳/抽象，不重写底层算法。
- **诚实记录**：诊断/审计先以报告形式呈现（`consistency_audit.md`），等用户"开始"再执行整改。
- **commit 风格**：中文 message（如 `docs: 增补核心模块注释（DeepSeek PoW 逆向项目）`）。

---

## 十、跨项目用户档案（~/.workbuddy/MEMORY.md 可保留）

- GitHub 账号：`mc-lhz`，邮箱用 `mc-lhz@users.noreply.github.com`
- 偏好最小改动封装（不重写底层）
- 偏好小驼峰命名（覆盖默认 PEP 8 类名 PascalCase）
- 公共习惯：先问"是否要做"，再问"做成什么样"，最后才执行（曾多次"先不要"）
- 对话中文；技术语言简洁结构化

---

## 十一、关键引用

- **GitHub 仓库**：https://github.com/mc-lhz/DeepseekPowChallenge
- **DeepSeek web**：https://chat.deepseek.com （web build 2.3.0）
- **数美 Shumei SDK**：fp-1.min.js（302KB，存于 `Analysis/`）
- **PoW WASM**：sha3_wasm_bg.7b9ca65ddd.wasm（算法名 DeepSeekHashV1）
- **运行时 Python**：D:\Program Files\Python\python.exe（3.11.7，curl_cffi 0.16.0）

---

*本文档由 WorkBuddy 在会话结束时按用户指令"将上下文输出到 md"自动生成；可作为项目迁移/交接的存档。如需精确到每一轮的对话节点，参考原始 daily log：`.workbuddy/memory/2026-08-09.md`*