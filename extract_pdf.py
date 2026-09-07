import pymupdf
doc = pymupdf.open(r"C:\Users\34239\Documents\GitHub\AI-tutor\guochuang.pdf")
print("PAGES:", len(doc))
with open(r"C:\Users\34239\Documents\GitHub\AI-tutor\guochuang_text.txt", "w", encoding="utf-8") as f:
    for i, page in enumerate(doc):
        t = page.get_text("text")
        f.write(f"\n===== PAGE {i+1} =====\n")
        f.write(t)
        print(f"page {i+1}: {len(t)} chars")
