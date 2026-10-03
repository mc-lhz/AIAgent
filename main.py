# coding=utf-8
import os
import sys
import time
import tools
from DeepseekChatClient.chatClient import deepseekClient
from ClaudeCodeClient import ClaudeCodeClient
from ClawCodeClient import ClawCodeClient

# ---- 配置常量集中 ----
MODEL = "deepseek-chat"
MODEL_CLAUDE = os.environ.get('CLAUDE_MODEL', 'claude-sonnet-4-20250514')
MODEL_CLAW = os.environ.get('CLAW_MODEL', 'claude-sonnet-4-20250514')
MAX_TURNS = 32
CONTEXT_WINDOW = 20
MAX_ENTRY_CHARS = 4000
RETRY_MAX = 3
BACKOFF_BASE = 0.5

# 后端优先级：claw > claude > deepseek
BACKEND_ORDER = ['claw', 'claude', 'deepseek']

# 初始化客户端（懒加载）
chatClient = None
chatClient_deepseek = None
chatClient_claude = None
chatClient_claw = None


def get_chat_client(force_backend=None):
    """获取活跃的 chat 客户端，支持动态切换"""
    global chatClient, chatClient_deepseek, chatClient_claude, chatClient_claw

    # 懒加载
    if chatClient_deepseek is None:
        chatClient_deepseek = deepseekClient()
    if chatClient_claude is None:
        try:
            chatClient_claude = ClaudeCodeClient(model=MODEL_CLAUDE)
        except Exception as e:
            print(f"[警告] Claude Code 初始化失败: {e}")
            chatClient_claude = None
    if chatClient_claw is None:
        try:
            chatClient_claw = ClawCodeClient(model=MODEL_CLAW)
        except Exception as e:
            print(f"[警告] Claw Code 初始化失败: {e}")
            chatClient_claw = None

    if force_backend in ('deepseek', 'claude', 'claw'):
        client = globals().get(f'chatClient_{force_backend}')
        if client:
            return client
        print(f"[警告] 后端 {force_backend} 不可用，回退到 Deepseek")
        return chatClient_deepseek

    # 按优先级返回第一个可用的后端
    for backend in BACKEND_ORDER:
        client = globals().get(f'chatClient_{backend}')
        if client:
            return client
    return chatClient_deepseek


# 启动时选择后端
chatClient = get_chat_client()
user_content = []

# 抽出的 prompt 构造：滑动窗口 + 单条截断
def build_prompt(user_input):
    window = user_content[-CONTEXT_WINDOW:]
    window_str = str([e[:MAX_ENTRY_CHARS] for e in window])
    tool_defs = [
        {"toolFunctionName": name, "description": getattr(tools, name).__doc__}
        for name in dir(tools) if callable(getattr(tools, name))
    ]
    return (
        "你将扮演一个智能助手，你可以执行各种任务，根据上下文判断是否需要停止执行\n"
        "输出：工具函数名(参数1，参数2...)，由于该表达式要输入eval，请注意字符串带引号，禁止输出其他内容。注意路径带4个反斜杠\n"
        # 已删除"不要轻易停止执行"——停止由 stop() 与 MAX_TURNS 共同保证
        "工具函数说明：\n" + str(tool_defs) + "\n"
        "上下文：\n" + window_str + "\n"
        "用户输入：\n" + user_input
    )

# 错误分类
def classify(err):
    s = str(err)
    if "401" in s or "Unauthorized" in s or "token" in s.lower():
        return "auth"       # 弃：需重新登录
    if "429" in s or "529" in s or "rate" in s.lower() or "timeout" in s.lower():
        return "throttle"   # 退避重试
    return "other"

# 带分类退避和自动 fallback 的 ask
_last_backend = None  # 跟踪当前后端


def ask_with_retry(prompt):
    global _last_backend, chatClient
    delay = BACKOFF_BASE

    # 尝试顺序：从当前/上次成功的后端开始，按 BACKEND_ORDER 依次 fallback
    if _last_backend:
        order = [_last_backend] + [b for b in BACKEND_ORDER if b != _last_backend]
    else:
        order = list(BACKEND_ORDER)

    tried_backends = set()

    for backend in order:
        if backend in tried_backends:
            continue
        tried_backends.add(backend)

        for attempt in range(RETRY_MAX):
            try:
                client = get_chat_client(force_backend=backend)
                result = client.ask(prompt, model=MODEL)
                # 成功，更新后端记录
                _last_backend = backend
                return result
            except Exception as e:
                if classify(e) == "auth":
                    raise
                if classify(e) == "throttle" and attempt < RETRY_MAX - 1:
                    time.sleep(delay)
                    delay *= 2
                    continue
                # 本次尝试失败，继续下一个后端
                _last_backend = backend  # 记录失败后端，下次跳过
                print(f"[后端 {backend} 失败: {e}] 尝试备用后端...")
                break  # 跳出当前后端的重试循环，换下一个

    # 所有后端都失败了
    raise RuntimeError("所有后端 (Claw Code, Claude Code, Deepseek) 均失败")

while True:
    user_input = input("请输入：")
    user_content.append(f"用户输入：{user_input}")
    # for 替代 while：硬上限 MAX_TURNS
    for turn in range(MAX_TURNS):
        prompt = build_prompt(user_input)
        try:
            ai_result = ask_with_retry(prompt)
        except Exception as e:
            if classify(e) == "auth":
                print(f"[认证失败] 请检查登录态：{e}")
                break                       # 回退到 input()
            print(f"AI Agent失败：{e}")
            break
        user_content.append(ai_result)      # 仅此处追加一次（已删原重复 append）
        try:
            tool_result = eval(f"tools.{ai_result}")
        except Exception as e:
            user_content.append(f"工具函数执行失败：{e}")
            continue
        if tool_result == '<stop>':
            break
        print(tool_result)
        user_content.append(f"你执行了{ai_result}，结果为{tool_result}")
    else:
        # for 正常跑满 MAX_TURNS 仍未 stop → 强制收尾，回到 input()
        print(f"[达到上限 {MAX_TURNS} 轮] 已自动停止，可继续输入新指令。")
