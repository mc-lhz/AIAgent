# coding=utf-8
import os
import sys
import numpy as np
import pandas as pd
import requests
import json
import subprocess
import time
import tools
from DeepseekChatClient.chatClient import deepseekClient
model = "deepseek-chat"
url = "https://api.deepseek.com/chat/completions"
# 密钥从环境变量读取，禁止硬编码（曾因硬编码上传公开仓库导致泄露）
api_key = os.getenv("DEEPSEEK_API_KEY", "")

headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

# 初始化ChatClient
chatClient = deepseekClient()
user_content = []

while True:
    continueFlag = True
    user_input = input("请输入：")
    user_content.append(f"用户输入：{user_input}")
    while continueFlag:
        
        
        prompt = (
            "你将扮演一个智能助手，你可以执行各种任务，根据上下文判断是否需要停止执行\n"
            "输出：工具函数名(参数1，参数2...)，由于该表达式要输入eval，请注意字符串带引号，禁止输出其他内容。注意路径带4个反斜杠\n"
            "不要轻易停止执行，如遇错误，请多次变换方法尝试\n"
            "工具函数说明：\n"
            + str(
                [
                [{"toolFunctionName": name, "description": getattr(tools, name).__doc__} for name in dir(tools) if callable(getattr(tools, name))]
                ]
            ) + "\n"
            "上下文：\n"
            + str(user_content) + "\n"
            "用户输入：\n"
            + user_input
        )
        # data = {
        #     "model": model,
        #     "messages": [
        #         {"role": "user", "content": prompt}
        #     ]
        # }
        try:
            ai_result = chatClient.ask(prompt, model=model)
        except Exception as e:
            user_content.append(f"AI Agent失败：{e}")
            continue
        user_content.append(ai_result)
        
        try:
            tool_result = eval(f"tools.{ai_result}")
        except Exception as e:
            user_content.append(f"工具函数执行失败：{e}")
            continue

        if tool_result == '<stop>':
            continueFlag = False
        print(tool_result)
        user_content.append(f"你执行了{ai_result}，结果为{tool_result}")