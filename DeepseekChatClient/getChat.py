"""DeepSeek 会话只读查询（无需 PoW）。

GET /api/v0/chat_session/fetch_page 与 /api/v0/chat/history_messages 仅作为列表/详情用途，
实测无需挂 X-DS-PoW-Response（只读 GET 通过 WAF 白名单）。鉴权仍由 deepseekAuthenticate.callWithReLogin
统一注入 bearer + cookie（来自 .env）。函数默认参数 BEARER/COOKIE="" 仅用于本文件独立调用测试。
"""
import curl_cffi.requests as requests
import json

from constants import apiBase, impersonate, xClientHeaders


def getChatList(API=apiBase, BEARER="", COOKIE=""):
    """列出会话：GET /api/v0/chat_session/fetch_page?lte_cursor.pinned=false

    实测无 PoW 也能过 WAF（只读 GET）；持久鉴权仍由 deepseekAuthenticate.callWithReLogin 注入。
    BEARER / COOKIE 默认空串仅用于本文件独立运行；返回解析后的 dict（已 .json()），与 getChatInfo 保持一致。
    响应结构（截取关键字段）：
    {
        "code": 0,
        "msg": "",
        "data": {
            "biz_code": 0,
            "biz_msg": "",
            "biz_data": {
                "chat_sessions": [
                    {
                        "id": "19abcefb-e854-4fdd-b55a-ec7417827133",
                        "title": "Chat1",
                        "title_type": "USER",
                        "pinned": false,
                        "model_type": "default",
                        "updated_at": 1786201281.978
                    },
                    {
                        "id": "3bf9b063-56ad-4065-857a-71715c582afd",
                        "title": "Chat2",
                        "title_type": "SYSTEM",
                        "pinned": false,
                        "model_type": "default",
                        "updated_at": 1740146128.939
                    }
                ],
                "has_more": false
            }
        }
    }
    """
    headers = {
        "Authorization": f"Bearer {BEARER}",
        "Cookie": COOKIE,
        "Referer": f"{API}/",
        **xClientHeaders,
    }
    resp = requests.get(
        f"{API}/api/v0/chat_session/fetch_page?lte_cursor.pinned=false",
        headers=headers,
        impersonate=impersonate,
    )
    return resp.json()
def getChatInfo(API=apiBase, BEARER="", COOKIE="", chatID=""):
    """获取会话详情：GET /api/v0/chat/history_messages?chat_session_id=<uuid>

    响应结构（截取关键字段）：
    {
        "code": 0,
        "msg": "",
        "data": {
            "biz_code": 0,
            "biz_msg": "",
            "biz_data": {
                "chat_session": {
                    "id": "********-********-********-********",
                    "title": "******",
                    "title_type": "USER",
                    "model_type": "default",
                    "pinned": false,
                    "updated_at": 1786201281.978,
                    "seq_id": 208364481,
                    "agent": "chat",
                    "version": 4,
                    "is_empty": false,
                    "current_message_id": 4,
                    "inserted_at": 1786200379.694
                },
                "chat_messages": [
                    {
                        "message_id": 1,
                        "parent_id": null,
                        "model": "",
                        "role": "USER",
                        "thinking_enabled": false,
                        "ban_edit": false,
                        "ban_regenerate": false,
                        "status": "FINISHED",
                        "incomplete_message": null,
                        "accumulated_token_usage": 37,
                        "feedback": null,
                        "inserted_at": 1786201257.783,
                        "search_enabled": true,
                        "fragments": [
                            {
                                "id": 1,
                                "type": "REQUEST",
                                "content": "<对话内容>"
                            }
                        ],
                        "has_pending_fragment": false,
                        "auto_continue": false,
                        "search_triggered": false
                    },
                    ==========================以下对话内容省略==========================
                ],
                "cache_control": "REPLACE",
                "cache_reset_at": 1786201533
            }
        }
    }
    """
    
    chatInfo = requests.get(
        f"{API}/api/v0/chat/history_messages?chat_session_id={chatID}",
        headers={"Authorization": f"Bearer {BEARER}",
                 "Cookie": COOKIE,
                 "Referer": f"{API}/",
                 **xClientHeaders},
        impersonate=impersonate,
    )
    return chatInfo.json()
def getParentMessageId(API=apiBase, BEARER="", COOKIE="", chatID=""):
    """获取最后一条消息的 message_id（作为下一条对话的 parent_message_id）。

    当会话首条（无消息）时返回 None；业务上 deepseekClient.getParentMessageId 会把它与「字段缺失/异常」
    统一处理为 0，调用方可直接传入 chat(prompt, chatSessionId, parentMessageId=0)。
    """
    chatInfo = getChatInfo(API=API, BEARER=BEARER, COOKIE=COOKIE, chatID=chatID)
    try:
        return chatInfo["data"]["biz_data"]["chat_messages"][-1]["message_id"]
    except IndexError:
        return None
    except KeyError:
        return None

    
