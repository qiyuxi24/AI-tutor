<script setup>
/**
 * DocWorkspace.vue — 文档阅读 / 编辑工作区（知识库与知识节点**共用同一个组件**）
 *
 * 为什么做成"数据源无关"：知识库文件与图谱节点小节都是"一段 Markdown + 可能有个原件"，
 * 阅读与编辑的交互完全一样。此前知识库与节点详情各写一套（textarea + 手写格式按钮），
 * 两份手感、两份 bug；现在收敛到这里，两处只是传进来的 `loadText` / `saveText` 不同。
 *
 * 分工（**这不是可有可无的抽象**，两边的差异全在这三个函数里）：
 *   · 知识库：`getKbNodeText(full)` / `saveKbNodeText` / `getKbNodeRaw` —— 文件节点
 *   · 知识节点：`fetchNodeSection` / `saveNodeSection` —— 小节化的平行 MD
 *     （老节点无 manifest 时退化成节点自身的 content，由调用方拼出同样的函数）
 *
 * 渲染分派（**阅读态**）：原件优先（PDF→iframe、图片→img、Word→docx-preview、
 * Excel→exceljs），拿不到原件（历史文件 404）静默回落解析正文；正文统一走
 * `utils/markdown.js`（全站同一套 marked + DOMPurify + katex）。
 * 为什么 PDF 用 iframe 不用 pdf.js：2026-09-28 实测 vue-pdf-embed 会把整本 300 页
 * 一次性画成 canvas，打开即卡死；iframe 交给浏览器原生阅读器按需渲染。
 *
 * **编辑态**走 `DocEditor`（Vditor，开源）：`ir` 即时渲染（默认，直接在正文里写）与
 * `sv` 分屏源码两模式随时切换。不把 Vditor 的渲染器带到阅读态 —— 否则同一个知识点
 * 在读和编辑时长得不一样。
 *
 * 未保存的修改：切换文档前由父级调 `canLeave()` 询问（父级持有"当前打开哪个"，
 * 本组件只判断脏不脏，不越权改父级状态）。
 *
 * Props:
 *   doc       Object  当前文档 `{ id, name, file_type | fileType? }`；null = 空态
 *   loadText  Function  async (doc) => { text, totalChars?, truncated?, editable?, binary? }
 *   saveText  Function  async (doc, text) => void（失败请抛异常，本组件展示 detail）
 *   loadRaw   Function  async (doc) => Blob      —— 可选，只有文件类文档才有原件
 *   bare      Boolean  嵌在别人的面板里（不画卡片外框、不画标题行）；默认 false
 *   lecture   Boolean  这份文档是**结构化讲义**（视频导入 + 已切片）：为真时头部多一个
 *                      「看讲义」主按钮，点它 emit `open-lecture` 切到课时页。
 *                      默认 false —— 本组件是数据源无关的，不该自己去查"它是不是视频"。
 * Emits: dirty  — 脏状态变化（父级可据此提示）
 *        open-lecture — 头部「看讲义」被点（父级负责切换到课时页）
 * Expose: canLeave()
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { confirmAction, notifySuccess, notifyWarning } from '../utils/feedback'
import { renderMarkdown } from '../utils/markdown.js'
import { previewKindOf } from '../utils/filePreview.js'
import { apiClient } from '../api'
import DocEditor from './DocEditor.vue'

const props = defineProps({
  doc: { type: Object, default: null },
  loadText: { type: Function, required: true },
  saveText: { type: Function, required: true },
  loadRaw: { type: Function, default: null },
  bare: { type: Boolean, default: false },
  /** 空态文案：不同场景说法不同（知识库"从左侧选择文件" / 节点"点击小节"） */
  emptyTitle: { type: String, default: '从左侧选择一个文件开始阅读' },
  emptyHint: { type: String, default: 'Markdown / 纯文本可以直接在右侧编辑并保存' },
  lecture: { type: Boolean, default: false },
})

const emit = defineEmits(['dirty', 'open-lecture'])

/* ── 读取态 ── */
const loading = ref(false)
const loadError = ref('')
const markdown = ref('')
const chars = ref(0)
const totalChars = ref(0)
const truncated = ref(false)
/** 由数据源判定的可编辑性（知识库按扩展名白名单；节点小节一律可编辑） */
const editable = ref(false)

/* ── 原件渲染态 ── */
const rawBlob = ref(null)
const objectUrl = ref('')
const renderError = ref('')
const rendering = ref(false)
const docxRef = ref(null)
const sheetRef = ref(null)

/* ── 编辑态 ── */
const editing = ref(false)
const editText = ref('')
const originalText = ref('')
const saving = ref(false)
const saveError = ref('')
/** 编辑模式：ir 即时渲染（默认，原生直接编辑）/ sv 分屏源码 */
const editorMode = ref('ir')

const mdRef = ref(null)
const assetUrls = []

const html = computed(() => renderMarkdown(markdown.value))
const dirty = computed(() => editing.value && editText.value !== originalText.value)

watch(dirty, (v) => emit('dirty', v))

/** 有原件才按原件分派；拿不到原件（历史数据 404）就回落正文。
 *  扩展名两处来源同义：知识库目录树给的是后端字段 `file_type`，节点侧传 `fileType` ——
 *  统一在这里读，别让调用方为了迁就本组件改名（改名会漏改一处，预览就悄悄退化成纯文本）。 */
const docExt = computed(() => props.doc?.fileType || props.doc?.file_type || '')
const kind = computed(() => (rawBlob.value ? previewKindOf(docExt.value) : 'text'))

function revoke() {
  if (objectUrl.value) {
    URL.revokeObjectURL(objectUrl.value)
    objectUrl.value = ''
  }
}

function revokeAssets() {
  while (assetUrls.length) URL.revokeObjectURL(assetUrls.pop())
}

onBeforeUnmount(() => {
  revoke()
  revokeAssets()
})

/* ── 原件渲染 ── */

async function renderDocx() {
  const { renderAsync } = await import('docx-preview')
  await nextTick()
  if (!docxRef.value) return
  docxRef.value.innerHTML = ''
  await renderAsync(rawBlob.value, docxRef.value, undefined, {
    className: 'docx-doc',
    inWrapper: true,
    ignoreWidth: true,   // 按容器宽度自适应，不还原 A4 页宽
    ignoreHeight: true,  // 连续滚动更好读
    useBase64URL: true,  // 图片内联，不再为资源多发请求
  })
}

/* Excel 渲染上限：几万行全量建 DOM 会把页面卡死，截断并明说 */
const MAX_ROWS = 200
const MAX_COLS = 30

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
  await wb.xlsx.load(await rawBlob.value.arrayBuffer())

  await nextTick()
  const host = sheetRef.value
  if (!host) return
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
        // textContent 逐格写入（不拼 HTML 串）：单元格内容同样不可信，建 DOM 天然免疫注入
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

async function prepareRaw() {
  revoke()
  objectUrl.value = URL.createObjectURL(rawBlob.value)
  rendering.value = true
  try {
    if (kind.value === 'docx') await renderDocx()
    else if (kind.value === 'xlsx') await renderXlsx()
  } catch (e) {
    renderError.value = `原件渲染失败：${e?.message || e}`
  } finally {
    rendering.value = false
  }
}

/* ── 正文 ── */

/**
 * 正文插图：`![](/api/v1/kb/slides/{key}/{n}.jpg)` 这类需要鉴权的接口，
 * `<img>` 发不出 Authorization 头（JWT 只在 axios 拦截器里）→ 用 axios 取 blob 换 objectURL。
 * 取不到就保留 alt 文案，不让整篇正文挂掉。
 */
async function hydrateImages() {
  const host = mdRef.value
  if (!host) return
  const imgs = Array.from(host.querySelectorAll('img')).filter((img) =>
    (img.getAttribute('src') || '').startsWith('/api/v1/kb/slides/')
  )
  for (const img of imgs) {
    const src = img.getAttribute('src')
    try {
      const { data } = await apiClient.get(src, { responseType: 'blob' })
      const url = URL.createObjectURL(data)
      assetUrls.push(url)
      img.src = url
    } catch {
      img.alt = img.alt || '截图加载失败'
    }
  }
}

/**
 * 正文里的链接一律新标签页打开：DOMPurify 默认不放行 `target`，写了也会被消毒掉；
 * 点图片同样开新标签页（板书/代码在小图里看不清，读大图交给浏览器）。
 */
function handleBodyClick(event) {
  const img = event.target?.closest?.('img')
  if (img?.src) {
    window.open(img.src, '_blank', 'noopener,noreferrer')
    return
  }
  const link = event.target?.closest?.('a[href]')
  if (!link) return
  event.preventDefault()
  window.open(link.getAttribute('href'), '_blank', 'noopener,noreferrer')
}

function reset() {
  revoke()
  revokeAssets()
  markdown.value = ''
  chars.value = 0
  totalChars.value = 0
  truncated.value = false
  editable.value = false
  rawBlob.value = null
  loadError.value = ''
  renderError.value = ''
  editing.value = false
  editText.value = ''
  originalText.value = ''
  saveError.value = ''
  saving.value = false
}

async function load() {
  const doc = props.doc
  if (!doc) return
  loading.value = true
  loadError.value = ''
  try {
    // 原件优先（只有文件类文档有 loadRaw）
    if (props.loadRaw && previewKindOf(docExt.value) !== 'text') {
      try {
        const blob = await props.loadRaw(doc)
        rawBlob.value = blob
        await prepareRaw()
        // 原件在手也取一次正文：编辑入口需要它，且 PDF 之外的格式正文可读
        const res = await props.loadText(doc)
        applyTextResult(res)
        return
      } catch {
        // 历史文件没存原件 → 静默回落正文（有解析文本可看，报"原件不存在"对用户无帮助）
        rawBlob.value = null
      }
    }
    const res = await props.loadText(doc)
    applyTextResult(res)
  } catch (e) {
    loadError.value = e.response?.data?.detail || e.message || '读取失败'
  } finally {
    loading.value = false
  }
}

function applyTextResult(res) {
  const d = res || {}
  markdown.value = d.text || ''
  chars.value = d.text ? d.text.length : 0
  totalChars.value = d.totalChars ?? chars.value
  truncated.value = !!d.truncated
  editable.value = !!d.editable
}

watch(
  () => props.doc?.id,
  () => {
    reset()
    if (props.doc) load()
  },
  { immediate: true }
)

// 正文变化后重新水合截图（编辑态 mdRef 不存在，函数内已判空）
watch(html, async () => {
  revokeAssets()
  await nextTick()
  hydrateImages()
})

/* ── 编辑 ── */

async function startEdit() {
  if (!props.doc) return
  loading.value = true
  try {
    // 必须拿全文：数据源的 loadText 对编辑场景应返回完整正文（知识库传 full=true）
    const res = await props.loadText({ ...props.doc, full: true })
    const text = res?.text ?? markdown.value
    editText.value = text
    originalText.value = text
    saveError.value = ''
    editing.value = true
    await nextTick()
    revokeAssets()
  } catch (e) {
    notifyWarning(e.response?.data?.detail || e.message || '读取正文失败')
  } finally {
    loading.value = false
  }
}

function cancelEdit() {
  if (dirty.value && !confirmAction('放弃未保存的修改吗？')) return
  editing.value = false
  editText.value = originalText.value
  saveError.value = ''
}

async function save() {
  if (!props.doc || saving.value) return
  saving.value = true
  saveError.value = ''
  try {
    await props.saveText(props.doc, editText.value)
    markdown.value = editText.value
    chars.value = editText.value.length
    totalChars.value = editText.value.length
    truncated.value = false
    originalText.value = editText.value
    editing.value = false
    notifySuccess('已保存')
    await nextTick()
    hydrateImages()
  } catch (e) {
    saveError.value = e.response?.data?.detail || e.message || '保存失败'
  } finally {
    saving.value = false
  }
}

/** 父级切文档前调用：脏了就问一句，返回 false 表示别切 */
function canLeave() {
  if (!dirty.value) return true
  return confirmAction('当前文件有未保存的修改，切换后将丢失。确定离开吗？')
}

defineExpose({ canLeave, startEdit })
</script>

<template>
  <div class="doc-ws" :class="{ 'doc-ws--bare': bare }">
    <!-- 空态 -->
    <div v-if="!doc" class="doc-empty">
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" /><line x1="8" y1="13" x2="16" y2="13" />
        <line x1="8" y1="17" x2="13" y2="17" />
      </svg>
      <p class="doc-empty-title">{{ emptyTitle }}</p>
      <p class="doc-empty-sub">{{ emptyHint }}</p>
    </div>

    <template v-else>
      <!-- 头部：标题 + 动作。bare 模式（嵌在节点详情里）只保留动作行 —— 标题与节点名重复，
           但**动作行不能省**：编辑入口就在这里，省了就没有进编辑态的路。 -->
      <header class="doc-head" :class="{ 'doc-head--bare': bare }">
        <div v-if="!bare" class="doc-head-main">
          <h2 class="doc-name" :title="doc.name">{{ doc.name }}</h2>
          <span v-if="editable" class="doc-tag">可编辑</span>
          <span v-if="!editing && kind === 'text' && totalChars" class="doc-chars">
            {{ truncated
              ? `已显示 ${chars.toLocaleString()} / ${totalChars.toLocaleString()} 字`
              : `${totalChars.toLocaleString()} 字` }}
          </span>
        </div>
        <div class="doc-actions">
          <!-- 「看讲义」放在编辑之前：讲义的价值在时间轴，不在原文。
               放在这里而不是另开一个入口，是因为用户从知识库点开它时，
               眼前就是这份转写稿 —— 他需要一个"这其实是一门课"的出口。 -->
          <button
            v-if="lecture && !editing"
            class="doc-btn doc-btn--lecture"
            title="打开课时页：按知识点看时间轴、截图，并一键回跳原视频"
            @click="emit('open-lecture', doc)"
          >看讲义</button>
          <template v-if="!editing">
            <button v-if="editable" class="doc-btn doc-btn--primary" @click="startEdit">编辑</button>
          </template>
          <template v-else>
            <button class="doc-btn" @click="cancelEdit">取消</button>
            <button class="doc-btn doc-btn--primary" :disabled="saving" @click="save">
              {{ saving ? '保存中...' : '保存' }}
            </button>
          </template>
        </div>
      </header>

      <!-- 编辑工具栏：模式切换（编辑时才有；动作按钮在上面那行） -->
      <div v-if="editing" class="doc-format">
        <div class="doc-mode-switch" role="group" aria-label="编辑模式">
          <button
            type="button"
            :class="{ on: editorMode === 'ir' }"
            title="即时渲染：直接在正文里写字（所见即所得）"
            @click="editorMode = 'ir'"
          >即时渲染</button>
          <button
            type="button"
            :class="{ on: editorMode === 'sv' }"
            title="源码模式：左边 Markdown，右边预览"
            @click="editorMode = 'sv'"
          >源码</button>
        </div>
      </div>

      <div class="doc-body">
        <p v-if="loading || rendering" class="doc-state">正在读取...</p>
        <p v-if="loadError" class="doc-state doc-state--error">{{ loadError }}</p>
        <p v-if="renderError" class="doc-state doc-state--error">{{ renderError }}</p>

        <!-- 原件：objectUrl 判空防 iframe 空 src 把应用页套进自己里 -->
        <iframe v-if="!loading && !editing && kind === 'pdf' && objectUrl" :src="objectUrl"
                :title="doc.name" class="doc-pdf" />
        <div v-if="!loading && !editing && kind === 'image'" class="doc-img">
          <img :src="objectUrl" :alt="doc.name" />
        </div>
        <div v-if="!loading && !editing && kind === 'docx'" ref="docxRef" class="doc-docx"></div>
        <div v-if="!loading && !editing && kind === 'xlsx'" ref="sheetRef" class="doc-sheet"></div>

        <!-- 编辑态：Vditor（ir 即时渲染 / sv 分屏源码） -->
        <div v-if="!loading && editing" class="doc-edit">
          <DocEditor v-model="editText" :mode="editorMode" />
          <p v-if="saveError" class="doc-save-error">{{ saveError }}</p>
        </div>

        <!-- 阅读态：解析正文 -->
        <template v-if="!loading && !editing && kind === 'text'">
          <p v-if="truncated" class="doc-state doc-state--tip">
            正文过长，仅显示前 {{ chars.toLocaleString() }} 字（共 {{ totalChars.toLocaleString() }} 字）。
            <template v-if="editable">点「编辑」可查看并修改全文。</template>
            <template v-else>该格式不支持在线编辑，需要全文请下载原件。</template>
          </p>
          <div ref="mdRef" class="doc-pane markdown-body" v-html="html" @click="handleBodyClick"></div>
        </template>
      </div>
    </template>
  </div>
</template>

<style scoped>
.doc-ws {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
/* bare：嵌在别的面板（如节点详情模态）里，外框与底色由父级给 */
.doc-ws--bare {
  background: transparent;
  border: none;
  border-radius: 0;
}

/* ── 空态 ── */
.doc-empty {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 40px;
  color: var(--color-text-muted);
}
.doc-empty-title {
  margin: 4px 0 0;
  font-size: 14px;
  color: var(--color-text-secondary);
}
.doc-empty-sub {
  margin: 0;
  font-size: 12px;
  color: var(--color-text-muted);
}

/* ── 头部 ── */
.doc-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 18px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}
/* bare：嵌在节点详情里 —— 标题那侧没有内容，动作行贴右并收紧内边距 */
.doc-head--bare {
  padding: 6px 0 6px 0;
  border-bottom: 1px solid var(--color-border);
  justify-content: flex-end;
}
.doc-head-main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.doc-name {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.doc-tag {
  flex-shrink: 0;
  padding: 1px 7px;
  border-radius: var(--radius-full);
  background: var(--color-green-light);
  color: var(--color-green);
  font-size: 11px;
  font-weight: 600;
}
.doc-chars {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--color-text-tertiary);
  white-space: nowrap;
}
.doc-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.doc-btn {
  padding: 5px 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-secondary);
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: var(--font-size-sm);
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.doc-btn:hover:not(:disabled) {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}
.doc-btn--primary {
  border-color: var(--color-accent);
  background: var(--color-accent);
  color: #fff;
}

/* 「看讲义」：主色描边而非实心 —— 它和「编辑」不是同一层级的动作
   （编辑改原文，看讲义换一种读法），两个实心按钮并排会让人不知道点哪个。 */
.doc-btn--lecture {
  border-color: var(--color-primary);
  color: var(--color-primary);
  font-weight: 500;
}
.doc-btn--lecture:hover:not(:disabled) {
  background: var(--color-primary-soft);
  color: var(--color-primary-dark);
}
.doc-btn--primary:hover:not(:disabled) {
  background: var(--color-accent);
  color: #fff;
  filter: brightness(1.08);
}
.doc-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ── 编辑工具栏（模式切换 + 动作） ── */
.doc-format {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-bg-secondary);
  flex-shrink: 0;
}
.doc-format-spacer {
  flex: 1 1 auto;
}
.doc-mode-switch {
  display: flex;
  gap: 2px;
  padding: 2px;
  border-radius: var(--radius-md);
  background: var(--color-bg-tertiary);
}
.doc-mode-switch button {
  padding: 3px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-tertiary);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.doc-mode-switch button:hover {
  color: var(--color-text-primary);
}
.doc-mode-switch button.on {
  background: var(--color-bg-card);
  color: var(--color-text-primary);
  box-shadow: var(--shadow-sm);
}

/* ── 主体 ── */
.doc-body {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.doc-state {
  margin: 0;
  padding: 12px 20px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--color-text-tertiary);
  flex-shrink: 0;
}
.doc-state--error { color: var(--color-red); }
.doc-state--tip {
  padding-bottom: 0;
}

.doc-pane {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 24px 32px;
}

/* 原件视图 */
.doc-pdf {
  flex: 1 1 auto;
  width: 100%;
  min-height: 0;
  border: 0;
  display: block;
}
.doc-img {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  display: flex;
  justify-content: center;
  padding: 16px;
}
.doc-img img {
  max-width: 100%;
  height: auto;
  border-radius: 8px;
}
.doc-docx,
.doc-sheet {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 16px 20px 28px;
}
.doc-docx :deep(.docx-wrapper) {
  background: transparent;
  padding: 0;
}
.doc-docx :deep(.docx-wrapper > section.docx) {
  box-shadow: none;
  padding: 0;
}
.sheet-block + .sheet-block { margin-top: 20px; }
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

/* ── 编辑区 ── */
.doc-edit {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 14px 14px;
}
.doc-save-error {
  margin: 0;
  font-size: 12px;
  color: var(--color-red);
  flex-shrink: 0;
}

/* ── Markdown 排版（阅读态） ── */
.markdown-body {
  font-size: var(--detail-font-size, 14px);
  line-height: 1.78;
  color: var(--color-text-primary);
  word-break: break-word;
}
.markdown-body :deep(p) { margin: 0 0 10px; }
.markdown-body :deep(strong) { font-weight: 600; }
.markdown-body :deep(a) { color: var(--color-accent); }
.markdown-body :deep(code) {
  background: var(--color-bg-surface);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: 'Consolas', monospace;
  font-size: 0.92em;
}
.markdown-body :deep(pre) {
  background: var(--color-bg-tertiary);
  color: var(--color-text-primary);
  padding: 14px 16px;
  border-radius: 10px;
  overflow-x: auto;
  margin: 12px 0;
  font-size: 0.92em;
}
.markdown-body :deep(pre code) {
  background: transparent;
  padding: 0;
  color: inherit;
}
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  margin: 20px 0 8px;
  font-weight: 600;
  color: var(--color-text-primary);
  line-height: 1.4;
}
.markdown-body :deep(h1) { font-size: 1.5em; }
.markdown-body :deep(h2) { font-size: 1.28em; }
.markdown-body :deep(h3) { font-size: 1.12em; }
.markdown-body :deep(blockquote) {
  border-left: 3px solid var(--color-accent);
  padding-left: 14px;
  margin: 12px 0;
  color: var(--color-text-secondary);
}
.markdown-body :deep(ul),
.markdown-body :deep(ol) { padding-left: 22px; margin: 8px 0; }
.markdown-body :deep(li) { margin: 3px 0; }
.markdown-body :deep(table) {
  border-collapse: collapse;
  margin: 12px 0;
  max-width: 100%;
  font-size: 0.94em;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid var(--color-border);
  padding: 6px 10px;
}
.markdown-body :deep(th) { background: var(--color-bg-secondary); font-weight: 600; }
.markdown-body :deep(hr) {
  border: none;
  border-top: 1px solid var(--color-border);
  margin: 20px 0;
}
.markdown-body :deep(img) {
  max-width: 100%;
  height: auto;
  border-radius: 8px;
  cursor: zoom-in;
}
</style>
