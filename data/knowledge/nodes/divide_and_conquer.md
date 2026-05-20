# 分治算法

## 基本定义
分治（Divide and Conquer）是一种重要的算法设计思想：将原问题分解为若干个规模更小的子问题，递归地求解子问题，再合并得到原问题的解。

## 三个步骤
1. **分解**：将问题分解为规模更小的子问题
2. **解决**：递归地求解子问题
3. **合并**：将子问题的解合并为原问题的解

## 与递归的关系
分治算法天然适合用递归实现，因为子问题与原问题结构相同。

## 经典例子
```python
# 二分查找
def binary_search(arr, target, left, right):
    if left > right:
        return -1
    mid = (left + right) // 2
    if arr[mid] == target:
        return mid
    elif arr[mid] > target:
        return binary_search(arr, target, left, mid - 1)
    else:
        return binary_search(arr, target, mid + 1, right)
```

## 常见应用
- 二分查找
- 归并排序
- 快速排序
