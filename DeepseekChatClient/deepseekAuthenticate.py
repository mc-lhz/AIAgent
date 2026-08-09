"""deepseekAuthenticate.py — DeepSeek 登录与鉴权统一管理（不使用 Session，手动维护 cookie 串）。

合并自原 auth.py（鉴权 / cookie 管理 / 自动重登）与原 loginAPI.py（密码登录 HTTP 原语 + 测试台）。

职责：
  1. 从 ./.env 读写 BEARER / COOKIE（key=value 格式；兼容旧版纯 bearer 文本）
  2. 登录原语 passwordLogin()：POST /api/v0/users/login
     - 该端点不在访客 PoW 白名单 → 不挂 X-DS-Guest-PoW-Response（无需 WASM PoW）
     - withToken:false → 不带 Authorization
     - device_id 字段必须出现（缺失 422），值可空
  3. doLogin()：调用 passwordLogin，从响应 Set-Cookie 提取并合并 cookie 串 + token，写回 .env
  4. isTokenInvalid(resp)：判断 token 是否失效
     （HTTP 401/403、或 body 顶层 code==40003、或 biz_code in (40003, 40001)）
  5. ensureLoggedIn() / callWithReLogin(apiFunc, ...)：确保已登录；
     调用 API 时若 token 失效则重新登录一次并重试（仅一次，避免死循环）

不使用 session：cookie 以字符串形式显式传入每个请求，登录时从 Set-Cookie 头解析。
依赖：getDeviceId（device_id 生成/解析）、constants（apiBase / impersonate / xClientHeaders）。

手动测试登录行为：
    "D:\Program Files\Python\python.exe" deepseekAuthenticate.py
（登录身份取 .env 的 MOBILE / PASSWORD；curl_cffi 仅系统 Python 3.11.7 已装。）
"""
import os
import random
import string

import curl_cffi.requests as requests

from getDeviceId import getDeviceId, genSmIdV2
from constants import apiBase, impersonate, xClientHeaders

# ============== 配置（登录凭证存于 .env：MOBILE / EMAIL / PASSWORD / AREA_CODE） ==============
# .env 路径基于本模块所在目录，避免依赖运行时当前工作目录（cwd）；
# 这样无论从哪个目录运行 startChat.py 都能正确定位 DeepseekChatClient/.env
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


# ============== .env 读写 ==============
def loadEnv():
    """读取 .env，返回 {"BEARER":..., "COOKIE":...}。
    兼容旧格式：整文件就是一行纯 bearer（无 '='）。"""
    env = {"BEARER": "", "COOKIE": ""}
    if not os.path.exists(ENV_PATH):
        return env
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        raw = f.read().strip()
    if not raw:
        return env
    # 旧格式：单行纯 bearer（不含 '='）
    if "\n" not in raw and "=" not in raw:
        env["BEARER"] = raw
        return env
    for line in raw.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def saveEnv(bearer, cookie):
    """更新 BEARER / COOKIE，保留 .env 中的其他键（如 MOBILE/PASSWORD）。"""
    env = loadEnv()
    env["BEARER"] = bearer
    env["COOKIE"] = cookie
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        for k, v in env.items():
            f.write(f"{k}={v}\n")


def loadCredentials():
    """从 .env 读取登录凭证，返回 dict(mobile/email/password/areaCode)。"""
    env = loadEnv()
    return {
        "mobile": env.get("MOBILE", ""),
        "email": env.get("EMAIL", ""),
        "password": env.get("PASSWORD", ""),
        "areaCode": env.get("AREA_CODE", "+86"),
    }


# ============== Cookie 解析工具 ==============
def _getSetCookies(resp):
    """从响应取所有 Set-Cookie 头（兼容 curl_cffi 的多值接口）。"""
    h = resp.headers
    try:
        if hasattr(h, "get_list"):
            return h.get_list("Set-Cookie")
    except Exception:
        pass
    single = h.get("Set-Cookie")
    return [single] if single else []


def extractCookies(resp, baseCookie=""):
    """从响应 Set-Cookie 提取 cookie，并与 baseCookie 合并（前者覆盖后者）。"""
    cookies = {}
    for part in baseCookie.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()
    for c in _getSetCookies(resp):
        nv = c.split(";", 1)[0].strip()
        if "=" in nv:
            k, v = nv.split("=", 1)
            cookies[k.strip()] = v.strip()
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


# ============== 登录原语：密码登录 HTTP 调用 ==============
def passwordLogin(API=apiBase, BEARER="", COOKIE="", email=None, password=None, mobile=None, areaCode="+86", deviceId=None):
    """密码登录原语：POST /api/v0/users/login

    还原自 main_current.js。该端点**不在访客 PoW 白名单**（无需 X-DS-Guest-PoW-Response 头，无需
    WASM 求解），但 schema 强校验 body 字段：
      - os                 固定 "web"
      - email 或 mobile    二选一（mobile 时附 area_code）
      - password
      - device_id          字段必填（缺失返 422），值可空（"" 也能登录）
    响应（成功）：
    {
        "code": 0, "msg": "",
        "data": {"biz_code": 0, "biz_msg": "",
                 "biz_data": {"user": {"id": "...", "token": "裸字符串"}}}
    }
    返回 curl_cffi Response，业务解析在 doLogin 里完成。
    """
    body = {"os": "web"}
    if email is not None:
        body["email"] = email
    if mobile is not None:
        body["mobile"] = mobile
        body["area_code"] = areaCode
    if password is not None:
        body["password"] = password
    if deviceId is not None:
        body["device_id"] = deviceId
    headers = {
        "Cookie": COOKIE,
        "Referer": f"{API}/",
        "Content-Type": "application/json",
        **xClientHeaders,
    }
    return requests.post(f"{API}/api/v0/users/login", json=body, headers=headers, impersonate=impersonate)


# ============== 登录编排：doLogin ==============
def doLogin():
    """执行一次密码登录，返回 (bearer, cookie)。失败抛 RuntimeError。"""
    cred = loadCredentials()
    if not (cred["mobile"] or cred["email"]) or not cred["password"]:
        raise RuntimeError("缺少登录凭证：请在 .env 配置 MOBILE(或 EMAIL) 与 PASSWORD")
    env = loadEnv()
    # HWWAF/HTTPS 链路需要 cookie 才能过；首次登录没有时，用现场生成的 smidV2 兜底（数美风控 OK）
    baseCookie = env["COOKIE"] or f"smidV2={genSmIdV2()}"
    deviceId = getDeviceId(baseCookie)   # 优先用 cookie 里的 smidV2 拼 "B" 前缀；没有则现生成
    resp = passwordLogin(
        mobile=cred["mobile"] or None,
        email=cred["email"] or None,
        password=cred["password"],
        areaCode=cred["areaCode"],
        deviceId=deviceId,
        COOKIE=baseCookie,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"登录 HTTP 失败: {resp.status_code} {resp.text[:200]}")
    d = resp.json()
    if d.get("data", {}).get("biz_code") != 0:
        raise RuntimeError(
            f"登录业务失败: {d.get('data', {}).get('biz_code')} "
            f"{d.get('data', {}).get('biz_msg')}"
        )
    bearer = d["data"]["biz_data"]["user"]["token"]
    cookie = extractCookies(resp, baseCookie)
    saveEnv(bearer, cookie)
    return bearer, cookie


# ============== 失效判定 ==============
def isTokenInvalid(resp):
    """判断响应是否表示 token 失效 / 鉴权失败。

    支持 curl_cffi Response 对象与已解析的 dict 两种输入。
    信号：HTTP 401/403，或 body 顶层 code==40003，或 biz_code in (40003, 40001)，
    或错误帧 msg 含 "authorization failed" / "invalid token"（聊天 SSE 流内判定也走这里）。
    """
    if isinstance(resp, dict):
        if resp.get("code") == 40003:
            return True
        if resp.get("data", {}).get("biz_code") in (40003, 40001):
            return True
        # 兜底：错误帧 msg 含鉴权失败提示（统一收口 chat SSE 流内的失效判定）
        msg = str(resp.get("msg", "")).lower()
        if "authorization failed" in msg or "invalid token" in msg:
            return True
        return False
    status = getattr(resp, "status_code", None)
    if status in (401, 403):
        return True
    if hasattr(resp, "json"):
        try:
            d = resp.json()
        except Exception:
            return False
        if d.get("code") == 40003:
            return True
        if d.get("data", {}).get("biz_code") in (40003, 40001):
            return True
    return False


# ============== 调用封装 ==============
def ensureLoggedIn():
    """确保 .env 中有有效 bearer 与 cookie；缺失则登录一次。返回 (bearer, cookie)。"""
    env = loadEnv()
    if env["BEARER"] and env["COOKIE"]:
        return env["BEARER"], env["COOKIE"]
    return doLogin()


def callWithReLogin(apiFunc, *args, **kwargs):
    """包装一次 API 调用：注入 (bearer, cookie)，若 token 失效则重新登录一次并重试。

    仅重试一次（避免：token 失效 → 重登 → 仍失效 → 再重登 → 死循环）。对流式 / 非流式响应都安全。
    returns: apiFunc 的返回值（Response 或 dict）。
    """
    bearer, cookie = ensureLoggedIn()
    resp = apiFunc(*args, BEARER=bearer, COOKIE=cookie, **kwargs)
    if isTokenInvalid(resp):
        print("[auth] 检测到 token 失效，重新登录一次…")
        bearer, cookie = doLogin()
        resp = apiFunc(*args, BEARER=bearer, COOKIE=cookie, **kwargs)
    return resp


# ============== 测试辅助（仅手动测试用，生产路径不涉及） ==============
def randPass(n=20):
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


def randEmail():
    return "no_such_user_" + randPass(8).lower() + "@example.com"


if __name__ == "__main__":
    # 验证 POST /api/v0/users/login 行为；身份取 .env 的 MOBILE / PASSWORD
    cred = loadCredentials()
    account = cred["mobile"] or cred["email"]
    pwd = cred["password"]
    if not account or not pwd:
        print("请在 .env 配置 MOBILE(或 EMAIL) 与 PASSWORD 后再运行测试")
        raise SystemExit(1)
    isMobile = account.isdigit()

    def _doLogin(deviceId):
        if isMobile:
            return passwordLogin(mobile=account, password=pwd, deviceId=deviceId)
        return passwordLogin(email=account, password=pwd, deviceId=deviceId)

    print("登录身份 :", "手机号" if isMobile else "邮箱", account)
    print("测试密码 :", "*" * len(pwd), f"(长度 {len(pwd)})\n")

    # ① 完全不传 device_id 字段 → 预期 422（服务端 schema 强校验字段缺失）
    r1 = _doLogin(None)
    print("=== ① 不带 device_id（字段缺失） ===")
    print("HTTP", r1.status_code, r1.text[:200], "\n")

    # ② 带 getDeviceId() 产出的 device_id → 期望 200；正确凭证则 biz_code:0 并带回 token
    dev = getDeviceId("")
    print("device_id :", dev, "(长度", len(dev), ")\n")
    r2 = _doLogin(dev)
    print("=== ② 带 device_id（B+smidV2） ===")
    print("HTTP", r2.status_code, r2.text[:200], "\n")
    if r2.status_code == 200:
        try:
            d = r2.json()
            if d.get("data", {}).get("biz_code") == 0:
                user = d["data"]["biz_data"]["user"]
                print("✅ 登录成功！")
                print("   user.id =", user["id"])
                print("   token   =", user["token"])
                print("   （前端会把它包成 {value: token, __version:\"0\"} 存 localStorage）")
            else:
                print("   业务码:", d.get("data", {}).get("biz_code"),
                      d.get("data", {}).get("biz_msg"))
        except Exception as e:
            print("   解析响应失败:", e)

    # ③ device_id = ""（字段在、值空）→ 期望 200（值可空，仅校验 key 存在）
    r3 = _doLogin("")
    print("\n=== ③ device_id=''（字段在、值空） ===")
    print("HTTP", r3.status_code, r3.text[:200])
