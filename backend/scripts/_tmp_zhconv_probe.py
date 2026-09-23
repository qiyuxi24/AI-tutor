"""一次性排查脚本（跑完即删）：确认 zhconv 的目标变体选择与公式/代码安全。"""
from pathlib import Path

import zhconv

out = Path(__file__).resolve().parents[1] / "_tmp_zhconv.txt"
SAMPLE = (
    "在計算機科學中，**樹**（{{langx|en|tree}}）是一種抽象資料型別，用來模擬具有樹狀結構性質的資料集合。"
    "實作方式有陣列與指標。資訊軟體與計程車的雷射印表機。"
)
MATH = "公式保持原样：$E=mc^2$，$\\frac{1}{2}$，\\sum_{i=1}^n i"

lines = ["=== 原样 ===", SAMPLE, MATH, ""]
for variant in ("zh-hans", "zh-cn", "zh-sg", "zh-tw"):
    lines.append(f"=== convert(..., '{variant}') ===")
    lines.append(zhconv.convert(SAMPLE, variant))
    lines.append(zhconv.convert(MATH, variant))
    lines.append("")

out.write_text("\n".join(lines), encoding="utf-8")
print("written:", out)
