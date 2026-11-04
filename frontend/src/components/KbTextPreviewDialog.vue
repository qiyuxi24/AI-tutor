<script setup>
/**
 * KbTextPreviewDialog.vue — 知识库文件预览弹窗（只读）
 *
 * 用途：知识库里选中一个文件 → 看它到底是什么。两种数据源，按扩展名二选一：
 *   - **原件**（`rawBlob`，来自 /kb/node/{id}/raw）
 *       · PDF  → `<iframe :src="blobUrl">`，交给浏览器内置阅读器（原生分页/缩放/检索）
 *       · 图片 → `<img :src="blobUrl">`（同样交给浏览器）
 *       · Word / Excel → docx-preview / exceljs 在前端解析成 DOM（浏览器不会渲染这两个）
 *   - **解析正文**（`markdown`，来自 /kb/node/{id}/text）→ 其余格式（含历史文件、pptx/csv/代码）
 *
 * 分派唯一真值 = `utils/filePreview.js::previewKindOf`（父组件用它决定拉哪份数据）。
 *
 * 去耦：纯展示组件，不调 API、不改数据；数据由父组件（KbPanel）拉好后传入。
 * 渲染包全部**动态 import**：exceljs ≈900KB、docx-preview ≈170KB，进主包会拖慢首屏。
 *
 * 为什么 PDF 不用 pdf.js 组件画：2026-09-28 实测 vue-pdf-embed 会把**整本文档所有页**
 * 一次性画成 canvas（300 页的书 = 300 张 2000×2800 的画布），滚动/打开直接卡死；
 * 容器内嵌 iframe 让浏览器原生阅读器接管，按需渲染 + 自带翻页缩放，零 JS 开销。
 *
 * Props:
 *   visible     Boolean — 弹窗显示态（v-model:visible）
 *   name        String  — 文件名（标题）
 *   fileType    String  — 扩展名（KB 的 documents.file_type，如 '.pdf'）
 *   rawBlob     Blob    — 原件；无（null）= 走正文模式
 *   markdown    String  — 解析正文（Markdown / 纯文本）
 *   chars       Number  — 本次返回的字符数
 *   totalChars  Number  — 正文总字符数（大于 chars 说明被截断）
 *   truncated   Boolean — 是否被截断
 *   loading     Boolean — 正在读取
 *
 * Emits:
 *   update:visible — 关闭弹窗
 *
 * 安全：正文是不可信输入（网页抓取 / 用户上传），**必须**走 renderMarkdown
 *      （内含 DOMPurify 消毒）后 v-html，禁止绕过。
 *      Excel 表格走 `textContent` 逐格写入 DOM（不是拼 HTML 串）—— 单元格内容同样不可信，
 *      拼串就得再消毒一遍，直接建 DOM 天然免疫注入。
 * 链接：正文里的 `<a href>` 由本组件拦截，统一**新标签页**打开（见 openInNewTab），
 *      避免把整个应用页面顶掉；不依赖 target 属性（DOMPurify 默认不放行它）。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { renderMarkdown } from '../utils/markdown.js'
import { previewKindOf } from '../utils/filePreview.js'
import { apiClient } from '../api'

const props = defineProps({
  visible: { type: Boolean, default: false },
  name: { type: String, default: '' },
  fileType: { type: String, default: '' },
  rawBlob: { type: Object, default: null },
  markdown: { type: String, default: '' },
  chars: { type: Number, default: 0 },
  totalChars: { type: Number, default: 0 },
  truncated: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['update:visible'])

const html = computed(() => renderMarkdown(props.markdown))

/* ── 渲染分派 ── */

const kind = computed(() => (props.rawBlob ? previewKindOf(props.fileType) : 'text'))
const wide = computed(() => kind.value !== 'text')
// PDF 交给浏览器阅读器时，整块高度都给它（滚动在 iframe 内部，外层别再滚一层）
const fill = computed(() => kind.value === 'pdf')

const objectUrl = ref('')
const renderError = ref('')
// 解析/渲染中：docx-preview、exceljs 都是同步解析，大文件要等几秒，不置态就是一片空白
const rendering = ref(false)
const docxRef = ref(null)
const sheetRef = ref(null)

function revoke() {
  if (objectUrl.value) {
    URL.revokeObjectURL(objectUrl.value)
    objectUrl.value = ''
  }
}

onBeforeUnmount(() => {
  revoke()
  revokeAssets()
})

/** Word：docx-preview 把 docx 直接转成 DOM 插进容器（不经过后端转换） */
async function renderDocx() {
  const { renderAsync } = await import('docx-preview')
  await nextTick()
  docxRef.value.innerHTML = ''
  await renderAsync(props.rawBlob, docxRef.value, undefined, {
    className: 'docx-doc',
    inWrapper: true,
    ignoreWidth: true,   // 按弹窗宽度自适应，不还原 A4 页宽
    ignoreHeight: true,  // 不强行按页高留白（预览场景连续滚动更好读）
    useBase64URL: true,  // 图片内联，避免为资源再发一轮请求
  })
}

/* Excel 渲染上限：动辄几万行的表全量建 DOM 会把页面卡死，截断并明说 */
const MAX_ROWS = 200
const MAX_COLS = 30

/** ExcelJS 的单元格值可能是富文本/公式/超链接/日期对象，统一转成显示串 */
function cellText(v) {
  if (v == null) return ''
  if (v instanceof Date) return v.toLocaleString()
  if (typeof v === 'object') {
    if (v.richText) return v.richText.map((t) => t.text).join('')
    if (v.text != null) return String(v.text)      // 超链接
    if (v.result != null) return String(v.result)  // 公式结果
    if (v.error) return String(v.error)
    return ''
  }
  return String(v)
}

async function renderXlsx() {
  const ExcelJS = (await import('exceljs')).default
  const wb = new ExcelJS.Workbook()
  await wb.xlsx.load(await props.rawBlob.arrayBuffer())

  await nextTick()
  const host = sheetRef.value
  host.innerHTML = ''

  wb.eachSheet((sheet) => {
    const rows = Math.min(sheet.rowCount, MAX_ROWS)
    const cols = Math.min(sheet.columnCount, MAX_COLS)

    const block = document.createElement('section')
    block.className = 'sheet-block'
    const title = document.createElement('h3')
    title.className = 'sheet-name'
    title.textContent = sheet.name
    block.appendChild(title)

    const table = document.createElement('table')
    table.className = 'sheet-table'
    for (let r = 1; r <= rows; r++) {
      const tr = document.createElement('tr')
      for (let c = 1; c <= cols; c++) {
        const td = document.createElement('td')
        td.textContent = cellText(sheet.getCell(r, c).value)
        tr.appendChild(td)
      }
      table.appendChild(tr)
    }
    block.appendChild(table)

    if (sheet.rowCount > rows || sheet.columnCount > cols) {
      const tip = document.createElement('p')
      tip.className = 'sheet-truncate'
      tip.textContent = `共 ${sheet.rowCount} 行 × ${sheet.columnCount} 列，仅显示前 ${rows} 行 × ${cols} 列`
      block.appendChild(tip)
    }
    host.appendChild(block)
  })
}

/* ── 讲义插图（幻灯片截图）──
 * 正文里的 `![](/api/v1/kb/slides/{key}/{n}.jpg)` 是**需要鉴权**的接口，而 `<img>`
 * 发不出 Authorization 头（JWT 只在 axios 拦截器里）。所以渲染后统一用 axios 取
 * blob 再换 objectURL；取不到就保留 alt 文案，不让整篇正文挂掉。
 */
const mdRef = ref(null)
const assetUrls = []

/* 截图视图：讲义里的幻灯片截图单独抽成网格（对齐 course2md 的「一屏纵览整堂课」）。
   只收 `/kb/slides/` 那批（正文里可能有别的图），数量 0 时视图切换不出现。 */
const slideImages = ref([])
const viewMode = ref('text')   // text = 正文 / slides = 截图网格
const zoom = ref(null)         // 放大中的图 { src, alt }

function revokeAssets() {
  slideImages.value = []
  zoom.value = null
  while (assetUrls.length) URL.revokeObjectURL(assetUrls.pop())
}

async function hydrateImages() {
  const host = mdRef.value
  if (!host) return
  const imgs = Array.from(host.querySelectorAll('img')).filter((img) =>
    (img.getAttribute('src') || '').startsWith('/api/v1/kb/slides/'))
  const found = []
  for (const img of imgs) {
    const src = img.getAttribute('src')
    try {
      const { data } = await apiClient.get(src, { responseType: 'blob' })
      const url = URL.createObjectURL(data)
      assetUrls.push(url)
      img.src = url
      found.push({ src: url, alt: img.alt || '' })
    } catch {
      img.alt = img.alt || '截图加载失败'
    }
  }
  slideImages.value = found
}

// 同时盯 `loading`：正文可能在弹窗打开后才到（父组件异步取 text），
// 只盯 html 会错过"先空后到"的那一次渲染
watch([html, () => props.loading], async () => {
  revokeAssets()
  await nextTick()
  hydrateImages()
})

// 换文件 / 关闭 → 回到正文视图（截图网格属于"这一篇"的临时视图，不该串到下一篇）
watch(() => [props.visible, props.name], () => {
  viewMode.value = 'text'
})

/** 打开（或换文件）时按 kind 准备渲染物；关闭时释放 objectURL。 */
watch(
  () => [props.visible, props.rawBlob],
  async ([visible]) => {
    revoke()
    revokeAssets()
    renderError.value = ''
    if (!visible || !props.rawBlob) return

    objectUrl.value = URL.createObjectURL(props.rawBlob)
    rendering.value = true
    try {
      // PDF / 图片把 blobUrl 直接交给浏览器（<iframe> / <img>），不需要 JS 参与
      if (kind.value === 'docx') await renderDocx()
      else if (kind.value === 'xlsx') await renderXlsx()
    } catch (e) {
      renderError.value = `原件渲染失败：${e?.message || e}`
    } finally {
      rendering.value = false
    }
  },
  { immediate: true }
)

/**
 * 正文里的链接一律**新标签页**打开：
 * 用 `window.open` 而不是靠 `target="_blank"`——DOMPurify 默认允许 `href`/`rel` 但**不放行
 * `target`**（实测当前版本），写了也会被消毒掉；且不走 `utils/markdown.js`（那是对话/图谱/
 * 画像三处共用管线，改它影响面太大）。这里只拦本组件容器内的 `<a>`，零共享改动。
 *
 * 点图片 = 放大（讲义里的截图常常要看清板书/代码，弹窗里只有 320px 宽时字是糊的）。
 */
function openInNewTab(event) {
  const img = event.target?.closest?.('img')
  if (img?.src) {
    zoom.value = { src: img.src, alt: img.alt || '' }
    return
  }
  const link = event.target?.closest?.('a[href]')
  if (!link) return
  event.preventDefault()
  window.open(link.getAttribute('href'), '_blank', 'noopener,noreferrer')
}
</script>

<template>
  <Transition name="modal">
    <div v-if="visible" class="modal-overlay" @click.self="emit('update:visible', false)">
      <div class="modal-panel" :class="{ 'modal-panel--wide': wide, 'modal-panel--fill': fill }">
        <div class="modal-header">
          <h2 class="modal-title">{{ name || '预览' }}</h2>
          <div class="modal-actions">
            <span v-if="!loading && !rawBlob && totalChars" class="chars-info">
              {{ chars.toLocaleString() }} / {{ totalChars.toLocaleString() }} 字符
            </span>
            <!-- 讲义有截图时才有这个切换：正文（图文混排） / 截图（一屏看完整堂课） -->
            <div v-if="slideImages.length" class="view-switch" role="group" aria-label="视图">
              <button type="button" :class="{ active: viewMode === 'text' }"
                      @click="viewMode = 'text'">正文</button>
              <button type="button" :class="{ active: viewMode === 'slides' }"
                      @click="viewMode = 'slides'">截图 {{ slideImages.length }}</button>
            </div>
            <button class="action-btn close-btn" title="关闭" @click="emit('update:visible', false)">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   stroke-width="2.5" stroke-linecap="round">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        </div>

        <div class="modal-body">
          <!-- 状态提示与内容容器**互斥**（不能用 v-else 链）：渲染中容器也得挂在 DOM 上，
               否则 renderDocx/renderXlsx 里 await nextTick() 后拿到的是 null ref -->
          <p v-if="loading || rendering" class="state-text">正在读取...</p>
          <p v-if="renderError" class="state-text">{{ renderError }}</p>

          <!-- 原件：PDF（浏览器内置阅读器）/ 图片 / Word / Excel -->
          <!-- objectUrl 判空是防 iframe 空 src 把当前应用页套进自己里 -->
          <iframe v-if="!loading && kind === 'pdf' && objectUrl" :src="objectUrl"
                  :title="name" class="pdf-frame" />
          <div v-if="!loading && kind === 'image'" class="img-view">
            <img :src="objectUrl" :alt="name" />
          </div>
          <div v-if="!loading && kind === 'docx'" ref="docxRef" class="docx-view"></div>
          <div v-if="!loading && kind === 'xlsx'" ref="sheetRef" class="sheet-view"></div>

          <!-- 解析正文（含历史文件回退、pptx/csv/代码等无渲染器的格式） -->
          <template v-if="!loading && kind === 'text'">
            <p v-if="truncated && viewMode === 'text'" class="truncate-tip">
              正文过长，仅显示前 {{ chars.toLocaleString() }} 字（共 {{ totalChars.toLocaleString() }} 字）。
            </p>
            <div ref="mdRef" v-show="viewMode === 'text'" class="markdown-body"
                 v-html="html" @click="openInNewTab"></div>

            <!-- 截图视图：点开看大图（板书/代码在小缩略图里是看不清的） -->
            <div v-if="viewMode === 'slides'" class="slide-grid">
              <button v-for="(s, i) in slideImages" :key="s.src" type="button"
                      class="slide-cell" @click="zoom = s">
                <img :src="s.src" :alt="s.alt || `截图 ${i + 1}`" loading="lazy" />
                <span class="slide-cap">{{ s.alt || `截图 ${i + 1}` }}</span>
              </button>
            </div>
          </template>
        </div>
      </div>

      <!-- 图片放大：点遮罩关闭 -->
      <div v-if="zoom" class="zoom-overlay" @click.self="zoom = null">
        <img :src="zoom.src" :alt="zoom.alt || '放大查看'" class="zoom-img" />
        <button type="button" class="zoom-close" @click="zoom = null">关闭</button>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
}

.modal-panel {
  width: min(760px, 92vw);
  max-height: 85vh;
  background: var(--color-bg-primary);
  border-radius: 14px;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-popup);
  overflow: hidden;
}

/* 原件视图（PDF/Excel/图片）：文字类窄栏够用，版面类给足宽度 */
.modal-panel--wide {
  width: min(1100px, 94vw);
  max-height: 92vh;
}

/* PDF：面板给足固定高度 → iframe height:100% 才拿得到确定值 */
.modal-panel--fill {
  height: 92vh;
}
.modal-panel--fill .modal-body {
  padding: 0;
  overflow: hidden;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.modal-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.modal-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.chars-info {
  font-size: 12px;
  color: var(--color-text-tertiary);
}

/* ── 视图切换（正文 / 截图）── */
.view-switch {
  display: inline-flex;
  padding: 2px;
  border-radius: var(--radius-md);
  background: var(--color-bg-hover);
}
.view-switch button {
  padding: 3px 10px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-tertiary);
  font-family: inherit;
  font-size: var(--font-size-xs);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.view-switch button:hover { color: var(--color-text-primary); }
.view-switch button.active {
  background: var(--color-bg-card);
  color: var(--color-text-primary);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08);
}

/* ── 截图画廊 ── */
.slide-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
}
.slide-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-card);
  overflow: hidden;
  cursor: zoom-in;
  text-align: left;
  font-family: inherit;
  transition: border-color 0.15s, transform 0.15s;
}
.slide-cell:hover { border-color: var(--color-border-strong); }
.slide-cell:focus-visible {
  outline: 2px solid var(--color-border-focus);
  outline-offset: 2px;
}
/* 不裁切：截图裁掉边缘就没有板书了；高度随图走，同一行由 grid 自动对齐 */
.slide-cell img {
  display: block;
  width: 100%;
  background: var(--color-bg-hover);
}
.slide-cap {
  padding: 0 8px 6px;
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── 放大查看 ── */
.zoom-overlay {
  position: fixed;
  inset: 0;
  z-index: 1100;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  background: rgba(0, 0, 0, 0.8);
  cursor: zoom-out;
}
.zoom-img {
  max-width: 92vw;
  max-height: 82vh;
  object-fit: contain;
  border-radius: var(--radius-md);
  background: #fff;
}
.zoom-close {
  padding: 6px 14px;
  border: 1px solid rgba(255, 255, 255, 0.35);
  border-radius: var(--radius-full);
  background: transparent;
  color: #fff;
  font-family: inherit;
  font-size: var(--font-size-xs);
  cursor: pointer;
}

.action-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  border-radius: 6px;
  padding: 4px;
}

.action-btn:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.modal-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px 24px;
}

.state-text,
.truncate-tip {
  margin: 0 0 12px;
  font-size: 12px;
  color: var(--color-text-tertiary);
  line-height: 1.6;
}

/* ── 原件视图 ── */

/* 浏览器内置 PDF 阅读器（原生分页 / 缩放 / 检索 / 打印） */
.pdf-frame {
  width: 100%;
  height: 100%;
  border: 0;
  display: block;
}

.img-view {
  display: flex;
  justify-content: center;
}
.img-view img {
  max-width: 100%;
  height: auto;
  border-radius: 8px;
}

.docx-view :deep(.docx-wrapper) {
  background: transparent;
  padding: 0;
}
.docx-view :deep(.docx-wrapper > section.docx) {
  box-shadow: none;
  padding: 0;
}

.sheet-block + .sheet-block {
  margin-top: 20px;
}
.sheet-name {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-primary);
}
.sheet-table {
  border-collapse: collapse;
  font-size: 12px;
  max-width: 100%;
}
.sheet-table td {
  border: 1px solid var(--color-border);
  padding: 4px 8px;
  color: var(--color-text-primary);
  white-space: nowrap;
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sheet-truncate {
  margin: 8px 0 0;
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
}
</style>
