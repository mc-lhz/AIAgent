'''
Logcat For Python
使用方法：
import Logcat
Log = Logcat.Logcat()
Log.i('Test','这是一条 info 日志')
Log.e('Test','这是一条 error 日志')
Log.w('Test','这是一条 warning 日志')
Log.d('Test','这是一条 debug 日志')
不同颜色日志级别
'''
VERSION = '1.0'
import sys,os
import datetime
import platform
import ctypes
# 启用 Windows 虚拟终端处理（ANSI 颜色支持）
if platform.system() == 'Windows':
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
    mode = ctypes.c_uint32()
    kernel32.GetConsoleMode(handle, ctypes.byref(mode))
    # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
    kernel32.SetConsoleMode(handle, mode.value | 0x0004)
class Logcat:
    COLORS = {
        'DEBUG': '\033[94m',    # 蓝
        'INFO': '\033[92m',     # 绿
        'WARNING': '\033[93m',  # 黄
        'ERROR': '\033[91m',    # 红
        'RESET': '\033[0m'
    }
    def __init__(self, outputFile=None, fmt=None, datefmt='%H:%M:%S'):
        self.outputFile = outputFile
        self.datefmt = datefmt
        # 默认模板，{levelname} 会在 log 方法中被替换为带颜色的版本
        self.fmt = fmt or '[{asctime}] [{tag}/{levelname}] {message}'
    def log(self, tag, level, msg):
        levelname = level.upper()
        color = self.COLORS.get(levelname, '')
        reset = self.COLORS['RESET']
        asctime = datetime.datetime.now().strftime(self.datefmt)
        # 只在 levelname 字段上应用颜色
        coloredLevel = f'{color}{levelname}{reset}' if color else levelname
        output = self.fmt.format(asctime=asctime, tag=tag, levelname=coloredLevel, message=msg)
        # 控制台输出（levelname 已带颜色标记）
        print(output)
        # 如果指定了文件，同时写入文件（不带颜色）
        if self.outputFile:
            plain = self.fmt.format(asctime=asctime, tag=tag, levelname=levelname, message=msg)
            with open(self.outputFile, 'a', encoding='utf-8') as f:
                f.write(plain + '\n')
    def i(self, tag, msg):
        self.log(tag, 'INFO', msg)
    def e(self, tag, msg):
        self.log(tag, 'ERROR', msg)
    def w(self, tag, msg):
        self.log(tag, 'WARNING', msg)
    def d(self, tag, msg):
        self.log(tag, 'DEBUG', msg)
if __name__ == '__main__':
    Log = Logcat(outputFile=None)
    Log.i('Test','这是一条 info 日志')
    Log.e('Test','这是一条 error 日志')
    Log.w('Test','这是一条 warning 日志')
    Log.d('Test','这是一条 debug 日志')
