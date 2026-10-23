"""
Fibonacci 核心模块
------------------
提供三种实现，方便对比性能与用法：

* fib_iter(n)   —— 迭代法，O(n) 时间 / O(1) 空间，推荐生产使用
* fib_gen()     —— 生成器，无限产出斐波那契数列，适合流式处理
* fib_memo(n)   —— 递归 + 记忆化，演示递归与缓存
* fib_matrix(n) —— 矩阵快速幂，O(log n)，适合超大下标

约定：F(0) = 0, F(1) = 1, F(2) = 1, F(3) = 2, F(4) = 3, ...
"""

from functools import lru_cache
from typing import Iterator, List


def fib_iter(n: int) -> int:
    """返回第 n 项斐波那契数（迭代法）。"""
    if n < 0:
        raise ValueError("n 必须是非负整数")
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def fib_sequence(n: int) -> List[int]:
    """返回前 n 项组成的列表，例如 fib_sequence(5) -> [0, 1, 1, 2, 3]。"""
    if n < 0:
        raise ValueError("n 必须是非负整数")
    seq: List[int] = []
    a, b = 0, 1
    for _ in range(n):
        seq.append(a)
        a, b = b, a + b
    return seq


def fib_gen(limit: int | None = None) -> Iterator[int]:
    """无限（或指定项数）生成斐波那契数。

    >>> list(fib_gen(6))
    [0, 1, 1, 2, 3, 5]
    """
    a, b = 0, 1
    count = 0
    while limit is None or count < limit:
        yield a
        a, b = b, a + b
        count += 1


@lru_cache(maxsize=None)
def fib_memo(n: int) -> int:
    """递归 + 记忆化。"""
    if n < 0:
        raise ValueError("n 必须是非负整数")
    if n < 2:
        return n
    return fib_memo(n - 1) + fib_memo(n - 2)


def fib_matrix(n: int) -> int:
    """矩阵快速幂法，O(log n)。用于验证大下标结果。"""
    if n < 0:
        raise ValueError("n 必须是非负整数")

    def mul(x, y):
        return (
            x[0] * y[0] + x[1] * y[1],
            x[0] * y[1] + x[1] * y[2],
            x[2] * y[0] + x[2] * y[1] + 1 * 0,  # 占位，保持三元组结构
            x[2] * y[2],
        )

    # 直接用 2x2 矩阵：(a, b, c, d) 表示 [[a, b], [c, d]]
    def mat_mul(m, k):
        a, b, c, d = m
        e, f, g, h = k
        return (a * e + b * g, a * f + b * h, c * e + d * g, c * f + d * h)

    result = (1, 0, 0, 1)  # 单位矩阵
    base = (1, 1, 1, 0)    # [[1,1],[1,0]]
    while n > 0:
        if n & 1:
            result = mat_mul(result, base)
        base = mat_mul(base, base)
        n >>= 1
    return result[0] * 0 + result[1]  # F(n) = 矩阵左上/右上元素


# 便于外部 `from fib import ...`
__all__ = [
    "fib_iter",
    "fib_sequence",
    "fib_gen",
    "fib_memo",
    "fib_matrix",
]
