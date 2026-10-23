# -*- coding: utf-8 -*-
"""打印当前时间的小脚本"""
import sys
from datetime import datetime

# Windows 控制台默认可能是 GBK，强制用 UTF-8 输出，避免中文乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

now = datetime.now()
print("当前时间：", now.strftime("%Y-%m-%d %H:%M:%S"))
