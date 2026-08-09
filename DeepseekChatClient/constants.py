"""DeepSeek 客户端共享常量（小驼峰命名，单一信息源）。

存放所有模块共用的静态配置，避免各文件重复定义导致漂移：
  - apiBase        : 服务基址
  - impersonate    : curl_cffi 浏览器指纹模拟目标
  - xClientHeaders : web 端统一携带的客户端遥测头

会话凭据（BEARER / COOKIE）由 deepseekAuthenticate.py 从 .env 读取并注入，不在此处。
登录凭证（MOBILE / EMAIL / PASSWORD / AREA_CODE）存放于 .env（详见 deepseekAuthenticate.py）。
"""
apiBase = "https://chat.deepseek.com"          # chat.deepseek.com 的 https 基址
impersonate = "chrome120"                      # curl_cffi 浏览器指纹模拟目标（绕过 HWWAF TLS/JA3 门禁）
# Web 端统一携带的客户端遥测头（来自浏览器 fetch 抓取；部分字段是 AWS WAF 标记客户端类型所需的）
xClientHeaders = {
    "accept": "*/*",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "priority": "u=1, i",
    "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Microsoft Edge";v="150"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "x-client-bundle-id": "com.deepseek.chat",
    "x-client-locale": "zh_CN",
    "x-client-platform": "web",
    "x-client-timezone-offset": "28800",
    "x-client-version": "2.3.0",
}
