"""调用方示例：实例化 deepseekClient（自动登录+绑定会话），直接 ask(prompt) 开聊。

厚封装用法：
    python startChat.py
"""
from chatClient import deepseekClient


def startChat(title="DeepseekHashV1"):
    """调用方入口：一行实例化即完成登录+会话绑定，之后只需 client.ask(prompt)。

    默认会话标题 "DeepseekHashV1" 是分析/调试用的固定会话（需先在 DeepSeek web 端手动创建同名会话）。
    找不到时脚本退出，不会自动新建会话。
    """
    client = deepseekClient(title)      # ← 实例化即自动登录 + 绑定会话

    print("BEARER:", client.bearer[:8] + "***")
    print("COOKIE:", (client.cookie[:24] + "***") if client.cookie else "(空)")

    if not client.chatSessionId:
        print(f"未找到标题为 {title} 的会话，请先创建")
        return

    while True:
        response = client.ask(input("请输入问题："))   # ← 调用方只需传 prompt
        print(response)


if __name__ == "__main__":
    startChat()
