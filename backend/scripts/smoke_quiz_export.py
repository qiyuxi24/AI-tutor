"""
出题导出冒烟测试 — 不依赖数据库/LLM，用样例题目跑通 Markdown / Word / PDF 三条导出链。

用法（backend 目录下）：
    python scripts/smoke_quiz_export.py
输出落在 backend/data/tmp_export/ 下，检查文件存在且非空即可。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.quiz.exporter import export_quiz  # noqa: E402

SAMPLE = [
    {
        "id": 1, "type": "single", "question": "在二叉树的先序遍历中，根结点的访问顺序是？",
        "options": [
            {"value": "A", "label": "最先访问根结点"},
            {"value": "B", "label": "最后访问根结点"},
            {"value": "C", "label": "根结点在左右子树之间访问"},
            {"value": "D", "label": "不确定"},
        ],
        "answer": ["A"], "analysis": "先序遍历顺序为：根 → 左子树 → 右子树，因此根结点最先被访问。",
        "points": 10, "knowledge_point": "二叉树遍历",
    },
    {
        "id": 2, "type": "multiple", "question": "下列关于平衡二叉树的说法，正确的有？（多选）",
        "options": [
            {"value": "A", "label": "任一结点的左右子树高度差不超过 1"},
            {"value": "B", "label": "查找时间复杂度为 O(log n)"},
            {"value": "C", "label": "插入后一定需要旋转"},
        ],
        "answer": ["A", "B"],
        "analysis": "AVL 树要求平衡因子绝对值 ≤ 1；插入后仅在失衡时才旋转。",
        "points": 10, "knowledge_point": "平衡二叉树",
    },
    {
        "id": 3, "type": "judge", "question": "中序遍历二叉搜索树可以得到递增序列。",
        "options": [], "answer": ["正确"],
        "analysis": "二叉搜索树性质：左 < 根 < 右，中序遍历即升序。",
        "points": 10, "knowledge_point": "二叉搜索树",
    },
    {
        "id": 4, "type": "fill", "question": "对含 n 个结点的完全二叉树，其高度为 ______。",
        "options": [], "answer": ["⌊log2 n⌋ + 1"],
        "analysis": "完全二叉树高度等于 log2(n) 向下取整加 1。",
        "points": 10, "knowledge_point": "完全二叉树",
    },
    {
        "id": 5, "type": "short_answer", "question": "请简述为什么栈可以用于非递归的中序遍历。",
        "options": [], "answer": ["栈可保存回溯路径，模拟递归调用栈"],
        "analysis": "递归的调用栈保存了待访问的祖先结点，用显式栈即可模拟该过程。",
        "points": 10, "knowledge_point": "栈与递归",
    },
]


def main() -> int:
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "tmp_export")
    os.makedirs(out_dir, exist_ok=True)

    ok = True
    for fmt in ("md", "docx", "pdf"):
        for with_answer in (True, False):
            payload, media_type, ext = export_quiz(
                SAMPLE, fmt=fmt, subject="二叉树遍历",
                difficulty="medium", with_answer=with_answer,
            )
            data = payload.encode("utf-8") if isinstance(payload, str) else payload
            name = f"二叉树遍历_{'试题' if with_answer else '学生卷'}.{ext}"
            path = os.path.join(out_dir, name)
            with open(path, "wb") as f:
                f.write(data)
            size = len(data)
            flag = "OK " if size > 0 else "FAIL"
            if size <= 0:
                ok = False
            print(f"[{flag}] {fmt:4s} with_answer={str(with_answer):5s} "
                  f"{size:>8d} bytes  {media_type}  -> {path}")

    # ── 内容巡检：三种格式都必须真的包含题干/答案/解析/总分 ──
    # md 用 Markdown 标记，docx/pdf 是纯文本排版，故断言分开写
    MD_NEEDLES = ("二叉树", "**A.** 最先访问根结点", "> **答案**", "> **解析**", "**总分**：50 分")
    PLAIN_NEEDLES = ("二叉树", "A. 最先访问根结点", "【答案】", "【解析】", "总分：50 分")

    def _check(label: str, text: str, needles=PLAIN_NEEDLES):
        nonlocal ok
        for needle in needles:
            hit = needle in text
            print(f"[{'OK ' if hit else 'FAIL'}] {label} 含 {needle!r}")
            ok = ok and hit

    md, _, _ = export_quiz(SAMPLE, fmt="md", subject="二叉树遍历")
    _check("markdown", md, MD_NEEDLES)

    docx_bytes, _, _ = export_quiz(SAMPLE, fmt="docx", subject="二叉树遍历")
    # docx 本质是 zip，正文在 word/document.xml
    import re
    import zipfile
    from io import BytesIO

    with zipfile.ZipFile(BytesIO(docx_bytes)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    docx_text = re.sub(r"<[^>]+>", "", xml)
    _check("docx", docx_text)

    try:
        import pymupdf as fitz
    except ImportError:
        import fitz

    pdf_bytes, _, _ = export_quiz(SAMPLE, fmt="pdf", subject="二叉树遍历")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pdf_text = "".join(page.get_text() for page in doc)
    print(f"[INFO] pdf 页数 = {doc.page_count}")
    doc.close()
    _check("pdf", pdf_text)

    # 学生卷不应出现答案/解析
    md_student, _, _ = export_quiz(SAMPLE, fmt="md", subject="二叉树遍历", with_answer=False)
    no_ans = "【答案】" not in md_student and "【解析】" not in md_student
    print(f"[{'OK ' if no_ans else 'FAIL'}] 学生卷不含答案与解析")
    ok = ok and no_ans

    print("\n结果：", "全部通过" if ok else "存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
