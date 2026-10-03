# coding=utf-8
"""Claw Code 客户端 - 通过 subprocess 调用 claw 二进制（Rust 实现，无沙箱版）

兼容 DeepseekClient / ClaudeCodeClient 的接口：ask() / ask_stream()
"""
import subprocess
import os
import json
import time
import threading
import queue

# claw 二进制路径（需先在 claw-code/rust 下 cargo build --release，或放入 PATH）
CLAW_BIN = os.environ.get('CLAW_BIN', 'claw')

# 默认超时（秒）
DEFAULT_TIMEOUT = 120


class ClawCodeClient:
    """调用 claw 二进制作为 AI 后端（--dangerously-skip-permissions 绕过沙箱）"""

    def __init__(self, model='claude-sonnet-4-20250514', cwd=None):
        self.model = model
        self.cwd = cwd or os.getcwd()
        self.process = None
        self.input_queue = queue.Queue()
        self.output_queue = queue.Queue()
        self.running = False
        self._lock = threading.Lock()

    def _start_process(self):
        """启动 claw 进程（一次性会话，多次交互）"""
        if self.process:
            return
        try:
            self.process = subprocess.Popen(
                [
                    CLAW_BIN, '--print', '-p',
                    '--dangerously-skip-permissions',  # 绕过沙箱
                    '--model', self.model,
                    '--output-format', 'json'
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                text=True,
                bufsize=1,
                env={**os.environ, 'CLAW_SANDBOX': 'false'}
            )
            self.running = True
            threading.Thread(target=self._reader, daemon=True).start()
        except FileNotFoundError:
            raise RuntimeError(
                f"claw 二进制未找到: {CLAW_BIN}。\n"
                f"请先在 claw-code/rust 下执行: cargo build --release\n"
                f"并把 target/release/claw 加入 PATH，或设置环境变量 CLAW_BIN"
            )

    def _reader(self):
        """读取 claw 的输出"""
        while self.running and self.process:
            try:
                line = self.process.stdout.readline()
                if not line:
                    break
                self.output_queue.put(line.rstrip('\n'))
            except Exception:
                break

    def _send(self, message: str):
        """发送消息到 claw"""
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
                self._send(prompt)
                response_lines = self._read_until_done(timeout)
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
                self._send(prompt)
                for line in iter(self.process.stdout.readline, ''):
                    if not line:
                        break
                    stripped = line.rstrip('\n')
                    if not stripped or '[DONE]' in stripped:
                        break
                    parsed = self._parse_single(stripped)
                    if parsed:
                        yield parsed
            except Exception as e:
                self.close()
                raise

    def _build_prompt(self, user_input: str) -> str:
        """构造 claw 的 prompt（仅传递用户输入，claw 内部处理 system/context）"""
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
        """解析单行 claw 输出，提取 content"""
        if not line or line.strip() == '[DONE]' or line.strip() == '':
            return ''
        try:
            data = json.loads(line)
            content = data.get('content') or data.get('text') or data.get('result') or ''
            if isinstance(content, list):
                return ''.join(
                    c.get('text', '') for c in content if isinstance(c, dict)
                )
            return content
        except (json.JSONDecodeError, ValueError):
            return line

    def close(self):
        """关闭 claw 进程"""
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
    client = ClawCodeClient()
    resp = client.ask("你好，请用一句话介绍自己")
    print(resp)
    client.close()