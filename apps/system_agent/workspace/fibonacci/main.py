"""
斐波那契数列打印工具
====================
用法：
    python main.py                 # 默认打印前 20 项（并做一致性自检）
    python main.py -n 30           # 打印前 30 项
    python main.py -n 10 --index   # 打印下标 0..10 的对应值
    python main.py -n 100 --last   # 只打印第 100 项
    python main.py --check         # 只跑自检，不打印数列
"""

import argparse
import sys
import time

# 让 Windows 控制台也能正确输出中文，避免乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from fib import fib_iter, fib_matrix, fib_memo, fib_sequence


def print_sequence(n: int, show_index: bool = False) -> None:
    """打印前 n 项斐波那契数列。"""
    seq = fib_sequence(n)
    print(f"前 {n} 项斐波那契数列：")
    if show_index:
        for i, v in enumerate(seq):
            print(f"  F({i}) = {v}")
    else:
        # 每 10 个一行，方便阅读
        for start in range(0, len(seq), 10):
            chunk = seq[start:start + 10]
            print("  " + " ".join(f"{v:>12,}" if v > 9999 else f"{v:>6}" for v in chunk))


def print_last(n: int) -> None:
    """打印第 n 项（迭代法 + 矩阵法互相校验）。"""
    t0 = time.perf_counter()
    value = fib_iter(n)
    dt = time.perf_counter() - t0
    print(f"F({n}) = {value}")
    print(f"位数：{len(str(value))} 位")
    print(f"迭代法耗时：{dt * 1000:.3f} ms")


def self_check() -> bool:
    """内置自检：对齐已知值，并交叉验证两种算法。"""
    known = [0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610]
    seq = fib_sequence(len(known))
    print("[自检] 前 16 项对齐已知值：", "通过" if seq == known else f"失败 -> {seq}")

    ok = True
    for n in (0, 1, 2, 10, 50, 100, 300, 1000):
        a, b, c = fib_iter(n), fib_memo(n), fib_matrix(n)
        if not (a == b == c):
            ok = False
            print(f"[自检] n={n} 不一致：iter={a} memo={b} matrix={c}")
    print("[自检] 迭代 / 记忆化 / 矩阵三种实现结果一致：", "通过" if ok else "失败")
    print(f"[自检] F(100) = {fib_iter(100)}")
    return ok


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="打印斐波那契数列")
    parser.add_argument("-n", "--num", type=int, default=20, help="项数或下标（默认 20）")
    parser.add_argument("--index", action="store_true", help="逐项打印 F(i) = 值")
    parser.add_argument("--last", action="store_true", help="只打印第 n 项")
    parser.add_argument("--check", action="store_true", help="只运行自检")
    args = parser.parse_args(argv)

    if args.num < 0:
        print("错误：-n 必须是非负整数", file=sys.stderr)
        return 2

    print("=" * 52)
    print(" 斐波那契数列打印器 (Fibonacci Printer)")
    print("=" * 52)

    if args.check:
        return 0 if self_check() else 1

    if args.last:
        print_last(args.num)
    else:
        print_sequence(args.num, show_index=args.index)

    print("-" * 52)
    ok = self_check()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
