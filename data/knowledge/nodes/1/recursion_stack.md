# 递归与栈

## 基本概念
递归函数的调用过程与栈（Stack）数据结构密切相关。每次函数调用都会在调用栈上创建一个新的栈帧。

## 递归调用栈
- **入栈**：每次递归调用，新栈帧压入栈顶
- **出栈**：递归返回时，栈帧弹出
- **后进先出**：最后调用的函数最先返回

## 栈溢出
递归深度过大时会导致 **Stack Overflow**。

```python
def infinite_recursion():
    return infinite_recursion()  # RecursionError
```

## 空间复杂度
递归深度为 n 时，调用栈占用 O(n) 的额外空间。
