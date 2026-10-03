# coding=utf-8
"""Claude Code 客户端 - 通过 subprocess 调用 Claude Code CLI（无沙箱版）"""
import subprocess
import threading
import queue
import os
import json
import time

# Claude Code CLI 路径（需要确保在 PATH 中，或指定绝对路径）
CLAUDE_BIN = os.environ.get('CLAUDE_BIN', 'claude')

# 默认超时（秒）
DEFAULT_TIMEOUT = 120


class ClaudeCodeClient:
    """调用 Claude Code CLI 作为 AI 后端"""

    def __init__(self, model='claude-sonnet-4-20250514', cwd=None):
        self.model = model
        self.cwd = cwd or os.getcwd()
        self.process = None
        self.input_queue = queue.Queue()
        self.output_queue = queue.Queue()
        self.running = False
        self._lock = threading.Lock()

    def _start_process(self):
        """启动 Claude Code 进程（一次性会话，多次交互）"""
        if self.process:
            return
        try:
            self.process = subprocess.Popen(
                [CLAUDE_BIN, '--print', '-p'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                text=True,
                bufsize=1,
                env={**os.environ, 'CLAUDE_CODE_SANDBOX': 'false'}
            )
            self.running = True
            threading.Thread(target=self._reader, daemon=True).start()
        except FileNotFoundError:
            raise RuntimeError(f"Claude Code CLI 未找到: {CLAUDE_BIN}")

    def _reader(self):
        """读取 Claude Code 的输出"""
        while self.running and self.process:
            try:
                line = self.process.stdout.readline()
                if not line:
                    break
                self.output_queue.put(line.rstrip('\n'))
            except Exception:
                break

    def _send(self, message: str):
        """发送消息到 Claude Code"""
        self.process.stdin.write(message + '\n')
        self.process.stdin.flush()

    def _read_until_done(self, timeout=DEFAULT_TIMEOUT):
        """读取直到收到 [DONE] 或超时"""
        lines = []
        start = time.time()
        while time.time() - start < timeout:
            try:
                line = self.output_queue.get(timeout=1)
                lines.append(line)
                if '[DONE]' in line:
                    break
            except queue.Empty:
                if not self.running:
                    break
        return lines

    def ask(self, prompt: str, model=None, timeout=DEFAULT_TIMEOUT) -> str:
        """发送 prompt，返回 AI 回复（同步接口，兼容 DeepseekClient）"""
        with self._lock:
            try:
                self._start_process()
                full_prompt = self._build_prompt(prompt)
                self._send(full_prompt)
                # 等待响应
                response_lines = self._read_until_done(timeout)
                # 解析响应（去掉 [DONE] 和空行，只保留 content 部分）
                response = self._parse_response(response_lines)
                return response
            except Exception as e:
                self.close()
                raise

    def ask_stream(self, prompt: str, model=None):
        """流式接口（生成器），兼容 DeepseekClient 的 askStream"""
        with self._lock:
            try:
                self._start_process()
                full_prompt = self._build_prompt(prompt)
                self._send(full_prompt)
                # 逐块读取（按行）
                for line in iter(self.process.stdout.readline, ''):
                    if not line:
                        break
                    stripped = line.rstrip('\n')
                    if not stripped or '[DONE]' in stripped:
                        break
                    # 只 yield content 部分
                    parsed = self._parse_single(stripped)
                    if parsed:
                        yield parsed
            except Exception as e:
                self.close()
                raise

    def _build_prompt(self, user_input: str) -> str:
        """构造 Claude Code 的 prompt（简化版，仅传递用户输入）"""
        # Claude Code 的 prompt 格式：
        # system: xxx
        # user: xxx
        return user_input

    def _parse_response(self, lines: list) -> str:
        """从输出行中提取纯文本内容"""
        parts = []
        for line in lines:
            parsed = self._parse_single(line)
            if parsed:
                parts.append(parsed)
        return ''.join(parts)

    def _parse_single(self, line: str) -> str:
        """解析单行 Claude Code 输出，提取 content"""
        # Claude Code 的 --print 输出可能包含各种格式
        # 尝试 JSON 或纯文本
        if not line or line.strip() == '[DONE]' or line.strip() == '':
            return ''
        try:
            # 尝试解析 JSON
            data = json.loads(line)
            # 取 content 字段（如果有）
            content = data.get('content') or data.get('text') or ''
            if isinstance(content, list):
                # 多段 content
                return ''.join(c.get('text', '') for c in content if isinstance(c, dict))
            return content
        except (json.JSONDecodeError, ValueError):
            # 不是 JSON，直接返回原始行
            return line

    def close(self):
        """关闭 Claude Code 进程"""
        with self._lock:
            self.running = False
            if self.process:
                try:
                    self.process.stdin.close()
                    self.process.wait(timeout=5)
                except Exception:
                    self.process.kill()
                finally:
                    self.process = None

    def __del__(self):
        self.close()


if __name__ == '__main__':
    # 快速测试
    client = ClaudeCodeClient()
    resp = client.ask("你好，请用一句话介绍自己")
    print(resp)
    client.close()