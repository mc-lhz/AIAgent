# coding=utf-8
"""兼容 OpenAI / Anthropic 协议的对话补全客户端（统一 SSE 流式）。

无论上游是否原生支持流式，本客户端均向上游请求 SSE 流，
再按 stream 参数决定返回「逐帧迭代器」或「拼接文本字符串」。

base 指向任意 OpenAI 兼容端点（只需 /chat/completions；Anthropic 请求在本层转换为 OpenAI 格式）。
可通过覆盖模块变量指向其它兼容服务，例如：
    import chatProtocol
    chatProtocol.base = "https://your-endpoint/v1"
"""

import requests
import json
import uuid

# 上游 base URL 与请求伪装 UA（可按需覆盖）
base = "https://opencode.ai/zen/v1"
userAgent = (
    "opencode/1.18.18"
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def generateOpenAIStream(upstreamResponse, returnRawFrames):
    """遍历上游 SSE 行，统一生成 OpenAI 格式输出。
    returnRawFrames=True  -> 逐帧 yield 原始 SSE 文本；
    returnRawFrames=False -> 解析并 yield 模型文本内容片段。"""
    for incomingLine in upstreamResponse.iter_lines(decode_unicode=True):
        if not incomingLine or not incomingLine.startswith("data:"):
            continue
        dataPayload = incomingLine[len("data:"):].strip()
        if dataPayload == "[DONE]":
            if returnRawFrames:
                yield "data: [DONE]\n\n"
            continue
        if returnRawFrames:
            yield incomingLine + "\n\n"
        else:
            try:
                parsedJson = json.loads(dataPayload)
                contentPiece = parsedJson.get("choices", [{}])[0].get("delta", {}).get("content")
                if contentPiece:
                    yield contentPiece
            except Exception:
                pass


def anthropicToOpenai(anthropicBody):
    """Anthropic /v1/messages 请求体 -> OpenAI /chat/completions 请求体（纯文本版）。"""
    anthropicBody = dict(anthropicBody)
    openAIBody = {}
    model = anthropicBody.get("model")
    if model:
        openAIBody["model"] = model

    messages = []
    system = anthropicBody.get("system")
    if system:
        if isinstance(system, str):
            systemText = system
        elif isinstance(system, list):
            systemText = "\n\n".join(
                part.get("text", "") for part in system
                if isinstance(part, dict) and part.get("text")
            )
        else:
            systemText = ""
        if systemText:
            messages.append({"role": "system", "content": systemText})

    for message in anthropicBody.get("messages", []):
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, str):
            messages.append({"role": role, "content": content})
        elif isinstance(content, list):
            text = "".join(
                block.get("text", "") for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
            messages.append({"role": role, "content": text})
        else:
            messages.append({"role": role, "content": content})
    openAIBody["messages"] = messages

    if "max_tokens" in anthropicBody:
        openAIBody["max_tokens"] = anthropicBody["max_tokens"]
    for key in ("temperature", "top_p", "stream"):
        if key in anthropicBody:
            openAIBody[key] = anthropicBody[key]
    if "stop_sequences" in anthropicBody:
        openAIBody["stop"] = anthropicBody["stop_sequences"]
    if "tools" in anthropicBody:
        openAIBody["tools"] = _convertTools(anthropicBody["tools"])
    if "tool_choice" in anthropicBody:
        converted = _convertToolChoice(anthropicBody["tool_choice"])
        if converted is not None:
            openAIBody["tool_choice"] = converted
    return openAIBody


def _convertTools(anthropicTools):
    """Anthropic tools -> OpenAI tools（input_schema -> parameters）。"""
    openaiTools = []
    for tool in anthropicTools or []:
        openaiTools.append({
            "type": "function",
            "function": {
                "name": tool.get("name"),
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {}),
            },
        })
    return openaiTools


def _convertToolChoice(anthropicToolChoice):
    """Anthropic tool_choice -> OpenAI tool_choice。"""
    if not anthropicToolChoice:
        return None
    choiceType = anthropicToolChoice.get("type")
    if choiceType == "auto":
        return "auto"
    if choiceType == "any":
        return "required"
    if choiceType == "tool":
        return {"type": "function", "function": {"name": anthropicToolChoice.get("name")}}
    return None


def safeStream(generator, label):
    """包裹流式生成器：中途异常时打印日志（避免静默断流），再干净地结束。"""
    try:
        for frame in generator:
            yield frame
    except Exception as exc:
        print(f"[proxy] stream error ({label}): {type(exc).__name__}: {exc}")


def sseEvent(eventName, dataPayload):
    """拼一个 Anthropic 风格 SSE 事件文本。"""
    return f"event: {eventName}\ndata: {json.dumps(dataPayload, ensure_ascii=False)}\n\n"


def mapFinishReason(finishReason):
    return {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "content_filter",
    }.get(finishReason, "end_turn")


def openaiSseToAnthropicSse(upstreamResponse):
    """上游 OpenAI SSE 流 -> Anthropic SSE 事件流（含 text 与 tool_use 双向转换）。"""
    messageId = f"msg_{uuid.uuid4().hex[:24]}"
    model = ""
    sentMessageStart = False
    textStarted = False
    textStopped = False
    toolBlocks = {}               # OpenAI tool_calls index -> {id, name, started, stopped}
    anthropicIndexByOpenai = {}   # OpenAI index -> Anthropic block index
    nextBlockIndex = 1            # 文本块固定 index=0，工具块从 1 开始
    hasToolUse = False
    sentMessageDelta = False
    sentMessageStop = False
    pendingStopReason = None
    latestUsage = None

    def startText():
        nonlocal textStarted
        if not textStarted and not textStopped:
            yield sseEvent("content_block_start", {
                "type": "content_block_start", "index": 0,
                "content_block": {"type": "text", "text": ""},
            })
            textStarted = True

    def stopText():
        nonlocal textStarted, textStopped
        if textStarted and not textStopped:
            yield sseEvent("content_block_stop", {"type": "content_block_stop", "index": 0})
            textStarted = False
            textStopped = True

    def flushTail():
        nonlocal textStarted, textStopped, sentMessageDelta, sentMessageStop
        frames = []
        if textStarted and not textStopped:
            frames.append(sseEvent("content_block_stop", {"type": "content_block_stop", "index": 0}))
            textStarted = False
            textStopped = True
        for oIdx, blk in toolBlocks.items():
            if blk["started"] and not blk["stopped"]:
                frames.append(sseEvent("content_block_stop", {
                    "type": "content_block_stop", "index": anthropicIndexByOpenai[oIdx],
                }))
                blk["stopped"] = True
        if not sentMessageDelta:
            stopReason = "tool_use" if hasToolUse else (pendingStopReason or "end_turn")
            frames.append(sseEvent("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": stopReason, "stop_sequence": None},
                "usage": {"output_tokens": (latestUsage or {}).get("output_tokens", 0)},
            }))
            sentMessageDelta = True
        if not sentMessageStop:
            frames.append(sseEvent("message_stop", {"type": "message_stop"}))
            sentMessageStop = True
        return frames

    for rawLine in upstreamResponse.iter_lines(decode_unicode=True):
        if not rawLine or not rawLine.startswith("data:"):
            continue
        dataPayload = rawLine[len("data:"):].strip()
        if dataPayload == "[DONE]":
            yield from flushTail()
            return
        try:
            chunk = json.loads(dataPayload)
        except Exception:
            continue

        if not sentMessageStart:
            messageId = chunk.get("id") or messageId
            model = chunk.get("model") or model
            yield sseEvent("message_start", {
                "type": "message_start",
                "message": {
                    "id": messageId, "type": "message", "role": "assistant",
                    "model": model, "content": [], "stop_reason": None,
                    "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            })
            sentMessageStart = True

        choices = chunk.get("choices") or []
        if not choices:
            usage = chunk.get("usage")
            if usage:
                latestUsage = {"input_tokens": usage.get("prompt_tokens", 0),
                               "output_tokens": usage.get("completion_tokens", 0)}
            continue
        delta = choices[0].get("delta") or {}

        contentPiece = delta.get("content")
        if contentPiece:
            if not textStarted:
                yield from startText()
            yield sseEvent("content_block_delta", {
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": contentPiece},
            })

        for tc in (delta.get("tool_calls") or []):
            hasToolUse = True
            oIdx = tc.get("index", 0)
            if oIdx not in toolBlocks:
                toolBlocks[oIdx] = {
                    "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
                    "name": None, "started": False, "stopped": False,
                }
                anthropicIndexByOpenai[oIdx] = nextBlockIndex
                nextBlockIndex += 1
            blk = toolBlocks[oIdx]
            if tc.get("id"):
                blk["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                blk["name"] = fn["name"]
            if not blk["started"] and blk["name"]:
                if textStarted and not textStopped:
                    yield from stopText()
                yield sseEvent("content_block_start", {
                    "type": "content_block_start",
                    "index": anthropicIndexByOpenai[oIdx],
                    "content_block": {"type": "tool_use", "id": blk["id"], "name": blk["name"]},
                })
                blk["started"] = True
            argsFragment = fn.get("arguments") or ""
            if argsFragment and blk["started"]:
                yield sseEvent("content_block_delta", {
                    "type": "content_block_delta",
                    "index": anthropicIndexByOpenai[oIdx],
                    "delta": {"type": "input_json_delta", "partial_json": argsFragment},
                })

        finishReason = choices[0].get("finish_reason")
        if finishReason and not sentMessageDelta:
            pendingStopReason = mapFinishReason(finishReason)

        usage = chunk.get("usage")
        if usage:
            latestUsage = {"input_tokens": usage.get("prompt_tokens", 0),
                           "output_tokens": usage.get("completion_tokens", 0)}

    yield from flushTail()


def openAIChatCompletion(headers, body, stream=True):
    """OpenAI 兼容：统一向上游发 SSE 流。
    stream=True  -> 返回 SSE 帧迭代器；stream=False -> 返回拼接后的模型文本字符串。"""
    body = dict(body)
    body["stream"] = True
    # 长读取超时：连接 10s，读取 600s（免费端点常慢/卡顿，避免中途断流）
    upstreamResponse = requests.post(f"{base}/chat/completions", headers=headers,
                                     json=body, stream=True, timeout=(10, 600))
    upstreamResponse.encoding = "utf-8"
    upstreamResponse.raise_for_status()
    chunkGenerator = generateOpenAIStream(upstreamResponse, stream)
    return safeStream(chunkGenerator, "openai") if stream else "".join(chunkGenerator)


def anthropicMessages(headers, body, stream=True):
    """Anthropic 兼容：把请求转成 OpenAI 格式打上游 /chat/completions，再把响应转回 Anthropic 格式。
    stream=True  -> 返回 Anthropic SSE 事件迭代器；
    stream=False -> 返回拼接后的模型文本字符串。"""
    openAIBody = anthropicToOpenai(body)
    openAIBody["stream"] = True
    # 长读取超时：连接 10s，读取 600s（免费端点常慢/卡顿，避免中途断流）
    upstreamResponse = requests.post(f"{base}/chat/completions", headers=headers,
                                     json=openAIBody, stream=True, timeout=(10, 600))
    upstreamResponse.encoding = "utf-8"
    upstreamResponse.raise_for_status()
    if stream:
        return safeStream(openaiSseToAnthropicSse(upstreamResponse), "anthropic")
    fullText = ""
    for rawLine in upstreamResponse.iter_lines(decode_unicode=True):
        if not rawLine or not rawLine.startswith("data:"):
            continue
        dataPayload = rawLine[len("data:"):].strip()
        if dataPayload == "[DONE]":
            continue
        try:
            chunk = json.loads(dataPayload)
        except Exception:
            continue
        choices = chunk.get("choices") or []
        if choices:
            contentPiece = choices[0].get("delta", {}).get("content")
            if contentPiece:
                fullText += contentPiece
    return fullText
