"""演示脚本：直接运行，展示四种用法（不依赖命令行参数）。"""

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from fib import fib_gen, fib_iter, fib_matrix, fib_sequence
from main import print_last, print_sequence, self_check


def main() -> int:
    print(">>> 1) 打印前 15 项")
    print_sequence(15)
    print()

    print(">>> 2) 逐项打印下标 0..8")
    print_sequence(9, show_index=True)
    print()

    print(">>> 3) 生成器前 10 项")
    print("  " + ", ".join(str(x) for x in fib_gen(10)))
    print()

    print(">>> 4) 大数：第 200 项（矩阵法 vs 迭代法）")
    print_last(200)
    print("矩阵法 F(200) =", fib_matrix(200))
    print("两者一致：", fib_iter(200) == fib_matrix(200))
    print()

    print(">>> 5) 自检")
    ok = self_check()
    print("\n全部通过 ✅" if ok else "\n存在失败 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
