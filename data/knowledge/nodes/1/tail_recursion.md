# 尾递归优化

## 基本定义
尾递归是指递归调用是函数体中最后执行的语句，且结果直接返回。

## 特征
- **最后执行**：递归调用是函数的最后一个操作
- **直接返回**：递归结果直接返回，不再加工
- **可优化**：编译器可以复用当前栈帧

```python
# 非尾递归 — 返回后还要做乘法
def factorial(n):
    return 1 if n <= 1 else n * factorial(n - 1)

# 尾递归 — 结果直接传递
def factorial_tail(n, acc=1):
    return acc if n <= 1 else factorial_tail(n - 1, acc * n)
```

## 尾递归优化（TCO）
支持 TCO 的语言会复用当前栈帧，将空间复杂度从 O(n) 降到 O(1)。

## 手动模拟
```python
def trampoline(f):
    while callable(f):
        f = f()
    return f
```
