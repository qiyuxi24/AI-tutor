/**
 * 文件预览渲染分派：扩展名 → 渲染器种类。
 *
 * 唯一真值（两个消费方，别各写一份）：
 *   - `KbPanel.openPreview` 据此决定去拉**原件**（/kb/node/{id}/raw）还是**解析正文**（/text）
 *   - `KbTextPreviewDialog` 据此决定用哪个渲染器渲染原件
 *
 * 没登记的扩展名（pptx / epub / fb2 / djvu / csv / 代码 / 文本…）一律 'text' =
 * 回退解析正文（marked 渲染）。这不是遗漏：pptx 无靠谱免费前端渲染器，
 * csv 是纯文本直接读得懂，没必要为它们引包。
 */
const PREVIEW_KIND = {
  '.pdf': 'pdf',
  '.docx': 'docx',
  '.xlsx': 'xlsx',
  '.xls': 'xlsx',
  '.png': 'image',
  '.jpg': 'image',
  '.jpeg': 'image',
  '.bmp': 'image',
  '.webp': 'image',
  '.gif': 'image',
  '.tiff': 'image',
  '.tif': 'image',
}

/** 扩展名（含点，来自 KB 的 documents.file_type）→ 'pdf' / 'docx' / 'xlsx' / 'image' / 'text' */
export const previewKindOf = (ext) => PREVIEW_KIND[(ext || '').toLowerCase()] || 'text'
