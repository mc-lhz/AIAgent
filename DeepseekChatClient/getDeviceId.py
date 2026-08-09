"""Device id 工具：复现数美 Shumei 指纹 smidV2，以及从 cookie 解析。

DeepSeek 登录/注册/发码请求 body 的 device_id 即数美 SDK 的 getDeviceId() 返回值：
    device_id = "B" + smidV2
本文件纯 Python 复现，不依赖浏览器。

函数（小驼峰）：
  smidFromCookie(cookie) -> 从 cookie 还原 smidV2 裸串
  genSmIdV2()            -> 现场生成一个合法 smidV2（63 字符）
  getDeviceId(cookie)    -> 返回登录要用的 device_id（"B"+smidV2）
"""
import re
import hashlib
import uuid
import datetime


def smidFromCookie(cookie):
    """从 cookie 还原数美 smidV2（device_id 的裸值）。"""
    m = re.search(r"smidV2=([0-9a-f]+)", cookie)
    return m.group(1) if m else None


def genSmIdV2():
    """Python 复现数美 fp-1.min.js 的 smidV2 生成算法。

    格式：<14位本地时间戳><md5(uuid_v4)>00<md5('smsk_web_'+前段)[:14]>0，共 63 字符。
    熵源用 str(uuid.uuid4())，等价于 Shumei 的 RFC-4122 v4 UUID polyfill。
    """
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")   # 14 位
    u = str(uuid.uuid4())                                    # RFC-4122 v4 UUID
    m1 = hashlib.md5(u.encode("utf-8")).hexdigest()          # 32 位
    b = ts + m1 + "00"                                       # 14+32+2 = 48
    m2 = hashlib.md5(("smsk_web_" + b).encode("utf-8")).hexdigest()[:14]  # 14 位
    return b + m2 + "0"                                      # + 末尾 "0" = 63


def getDeviceId(cookie=None):
    """返回登录 body 要用的 device_id 字符串。

    优先用 cookie 里的 smidV2（拼 "B" 前缀）；没有则现场生成一个。
    注意：服务端不校验 device_id 值真伪（实测 "" 也能登录），此处只为生成形似的值。
    """
    smId = smidFromCookie(cookie) if cookie else None
    if not smId:
        smId = genSmIdV2()
    return "B" + smId
