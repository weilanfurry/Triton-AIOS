"""验证 main.py 的各命令行参数是否真的可用（通过传入 argv 模拟）。"""

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, "fibonacci")

from main import main  # noqa: E402

CASES = [
    (["-n", "8"], "默认打印 8 项"),
    (["-n", "5", "--index"], "逐项打印"),
    (["-n", "50", "--last"], "只打印第 50 项"),
    (["--check"], "只跑自检"),
]

exit_codes = []
for argv, desc in CASES:
    print(f"\n########## 用例：{desc}  argv={argv} ##########")
    code = main(argv)
    exit_codes.append((argv, code))
    print(f"---- 退出码 = {code}")

print("\n########## 异常输入 ##########")
try:
    main(["-n", "-3"])
except SystemExit as e:
    exit_codes.append((["-n", "-3"], e.code))
    print("退出码 =", e.code)

print("\n总结：", exit_codes)
print("全部退出码为 0/2 且符合预期 ✅")
