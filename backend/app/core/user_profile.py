"""
用户画像核心模块

每个用户对应一个 Markdown 文件（data/profiles/{user_id}.md），
包含用户的基本信息、学习状态、性格特点等。

AI 可以读取和编辑此文件来更好地个性化教学。
"""

from pathlib import Path
from typing import Optional

# 默认用户画像模板
DEFAULT_PROFILE_TEMPLATE = """# 用户画像

## 基本信息
- **姓名/昵称**：（待填写）
- **年龄**：（待填写）
- **年级/阶段**：（待填写，如：高一、大二、工作中）

## 学习状态
- **当前学习目标**：（待填写，如：备战高考数学、学习 Python 编程）
- **知识背景**：（待填写，如：已掌握初中数学、有 JS 基础）
- **学习节奏偏好**：（待填写，如：喜欢慢节奏深入理解、喜欢快速过一遍再回头）
- **每周学习时间**：（待填写，如：每天 1 小时、周末集中学）

## 性格与偏好
- **性格特点**：（待填写，如：内向谨慎、外向主动、容易焦虑）
- **喜欢的教学方式**：（待填写，如：喜欢苏格拉底式提问、喜欢先看例子再学理论）
- **需要避免的方式**：（待填写，如：不喜欢被催促、不喜欢太抽象的讲解）

## AI 教学笔记
> 以下是 AI 在教学中观察到的内容，可随时更新。
>
> （待 AI 填写，如：该学生对概念理解较快但做题容易粗心，建议多出小练习检验）
"""


class UserProfile:
    """用户画像管理器（每个用户一个 MD 文件）"""

    def __init__(self, user_id: int, data_dir: Optional[Path] = None):
        if data_dir is None:
            data_dir = Path(__file__).parent.parent.parent.parent / "data" / "profiles"
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.user_id = user_id
        self.file_path = self.data_dir / f"{user_id}.md"

    def exists(self) -> bool:
        """检查用户画像文件是否存在"""
        return self.file_path.exists()

    def get(self) -> str:
        """获取用户画像内容，如果不存在则返回默认模板"""
        if self.file_path.exists():
            return self.file_path.read_text(encoding="utf-8")
        return DEFAULT_PROFILE_TEMPLATE

    def save(self, content: str) -> None:
        """保存用户画像内容（全量覆盖）"""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(content, encoding="utf-8")

    def update(self, content: str, mode: str = "replace") -> str:
        """
        更新用户画像内容。
        
        参数:
            content: 新内容
            mode: "replace" 全量替换 | "append" 追加到末尾
        
        返回: 更新后的完整内容
        """
        if mode == "append":
            current = self.get()
            updated = current.rstrip() + "\n\n" + content
        else:
            updated = content
        self.save(updated)
        return updated

    def get_summary(self) -> str:
        """
        获取用户画像的精简摘要（用于注入到 AI 系统提示词中）。
        如果画像不存在，返回空字符串。
        """
        if not self.file_path.exists():
            return ""
        content = self.file_path.read_text(encoding="utf-8")
        # 直接返回完整内容（Markdown 格式），AI 能理解
        return content
