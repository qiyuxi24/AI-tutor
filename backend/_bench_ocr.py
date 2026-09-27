"""临时基准：本地 RapidOCR 串行 vs 页级并发的吞吐（用完即删）"""
import os
import time
from concurrent.futures import ThreadPoolExecutor

import fitz

from app.core.kb.parsers.ocr import have_ocr, ocr_lines

N = 8


def make_page_png() -> bytes:
    doc = fitz.open()
    try:
        page = doc.new_page(width=1240, height=1754)
        txt = "\n".join(
            f"{i}. 二叉树的遍历：前序、中序、后序的时间复杂度都是 O(n)，空间 O(h)。"
            for i in range(28)
        )
        page.insert_textbox(fitz.Rect(60, 60, 1180, 1700), txt, fontsize=16,
                            fontname="china-s")
        return page.get_pixmap(dpi=200).tobytes("png")
    finally:
        doc.close()


def bench(png: bytes, workers: int) -> float:
    imgs = [png] * N
    t0 = time.perf_counter()
    if workers <= 1:
        for im in imgs:
            ocr_lines(im)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(ocr_lines, imgs))
    return time.perf_counter() - t0


def main() -> None:
    cpus = os.cpu_count() or 1
    print(f"have_ocr={have_ocr()} cpus={cpus}")
    png = make_page_png()
    lines = ocr_lines(png)
    print(f"warmup ok, lines={len(lines or [])}, png={len(png) // 1024}KB")
    for w in [1, 2, 4, 8, cpus]:
        if w > cpus:
            continue
        t = bench(png, w)
        print(f"workers={w:2d}: {t:6.2f}s / {N} 页 → {t / N:.2f}s/页")


if __name__ == "__main__":
    main()
