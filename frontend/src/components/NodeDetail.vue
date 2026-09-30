<script setup>
/**
 * NodeDetail.vue — 节点详情弹窗
 *
 * 职责：展示节点详情 + 编辑 Markdown 内容。
 *
 * 去耦设计：
 *   - 保存操作通过 emit('save-content', { nodeId, content }) 通知父组件
 *   - 父组件（HomeView）调用 Store.updateNodeContent() 执行保存
 *   - 组件不直接调用 apiClient
 *
 * Props:
 *   nodeInfo: Object  — 节点详情数据
 *   visible: Boolean  — 弹窗显示状态
 *
 * Emits:
 *   close: ()                  — 关闭弹窗
 *   refresh: ()                — 请求父组件刷新数据
 *   save-content: ({ nodeId, content }) — 保存节点内容（父组件调用 Store）
 */

import { ref, computed, watch } from 'vue'
import { renderMarkdown } from '../utils/markdown.js'
// ★ 保存操作由父组件通过 Store 处理；此处只读小节正文/试题列表与触发生成（走 kb.js 封装，不裸调 apiClient）
import { fetchNodeSection, fetchNodeQuizzes, generateSectionQuiz } from '../api/kb.js'
import { formatError } from '../utils/errorCodes.js'
import { useDetailPrefs } from '../utils/detailPrefs.js'

const props = defineProps({
  nodeInfo: { type: Object, default: null },
  visible: { type: Boolean, default: false },
  // 来源高亮：当前在图谱里高亮的资料 doc_id（来自知识库右键「在图谱中显示」）。
  // 非空时，属于这份资料的小节与题目会加醒目样式，并自动打开第一个命中小节。
  sourceDocId: { type: [Number, String], default: null },
})

const emit = defineEmits(['close', 'refresh', 'save-content', 'navigate-to-node', 'open-quiz'])

const mode = ref('view')        // 'view' | 'edit'
const editContent = ref('')
const saving = ref(false)
const saveError = ref('')

/* 节点小节化：仅 nodeInfo.has_sections === true 时启用（老节点行为完全不变） */
const hasSections = computed(() => props.nodeInfo?.has_sections === true)
const sections = computed(() => props.nodeInfo?.sections || [])

/* 阅读模式**恒有**左侧栏（老节点只列一项「正文」）。
   曾经左侧栏只在 has_sections=true 时渲染，而「生成小节」按钮又在栏内
   → 没小节就没有栏、没有栏就没有按钮，小节永远生成不出来（死锁，2026-09-27 修）。 */
const LEGACY_DOC = { id: '__content__', title: '正文', kind: 'custom', status: 'filled' }
const docList = computed(() => (hasSections.value ? sections.value : [LEGACY_DOC]))
const activeSectionId = ref('')
const sidebarCollapsed = ref(false)     // 侧栏可伸缩：收起后只剩一条 36px 窄边 + 展开按钮
const sectionContent = ref('')          // 当前选中节正文（懒加载）
const sectionHtml = computed(() => renderMarkdown(sectionContent.value))
const sectionLoading = ref(false)
const sectionError = ref('')

/* 试题分组：与小节同侧边栏，懒加载（打开节点详情时请求一次；任意节点都展示） */
const quizzes = ref([])
const quizzesLoading = ref(false)
const quizzesError = ref('')

/* 题型 → 中文短标签（列表项徽标；未知类型兜底显示原值） */
const QUIZ_TYPE_LABELS = {
  single: '单选',
  multiple: '多选',
  judge: '判断',
  fill: '填空',
  short_answer: '简答',
}
function quizTypeLabel(t) {
  return QUIZ_TYPE_LABELS[t] || t
}

/** 题干截断（列表项约 40 字，超出省略；全文放 title 由模板负责） */
function truncateText(s, n = 40) {
  const t = (s || '').trim()
  return t.length > n ? t.slice(0, n) + '…' : t
}

/** 由 section_id 反查小节标题（用于「属 xxx」归属标记） */
function sectionTitle(id) {
  const s = sections.value.find(x => x.id === id)
  return s ? s.title : id
}

/* 小节级溯源：当前选中节带了 sources 就在正文上方标出「来源：资料·章节」
   （条目形状来自 manifest，见 KnowledgeGraph.create_section；老 manifest 无此键 → 空） */
const activeSources = computed(() => {
  const s = sections.value.find(x => x.id === activeSectionId.value)
  return s?.sources || []
})

/* 「在图谱中显示」的来源高亮：属于该高亮资料的小节/题目加醒目样式。
   doc_id 比对统一字符串化（后端两处分别来自 JSON 与 SQL，类型不保证一致）。 */
const highlightDocId = computed(() => {
  const id = props.sourceDocId
  return id === null || id === undefined || id === '' ? '' : String(id)
})
function sectionHasSource(s) {
  return !!highlightDocId.value
    && (s.sources || []).some(x => String(x.doc_id) === highlightDocId.value)
}
function quizHasSource(q) {
  return !!highlightDocId.value
    && (q.source_docs || []).some(d => String(d.doc_id) === highlightDocId.value)
}
function sourceLabel(s) {
  const name = s.doc_name || '未知资料'
  return s.section ? `${name} · ${s.section}` : name
}

/**
 * 懒加载该节点的试题：打开节点详情时请求一次。
 * 副作用：更新 quizzes / quizzesLoading / quizzesError。失败不清屏，仅就地在分组内提示。
 */
async function loadQuizzes() {
  quizzesLoading.value = true
  quizzesError.value = ''
  try {
    const { data } = await fetchNodeQuizzes(props.nodeInfo.id)
    quizzes.value = data?.quizzes || []
  } catch (e) {
    quizzes.value = []
    quizzesError.value = formatError(e, { action: '加载试题' })
  } finally {
    quizzesLoading.value = false
  }
}

/** 题目来源文件（侧边栏一行展示；无来源 → 空串不占位） */
function quizDocLabel(q) {
  return (q.source_docs || []).map(d => d.doc_name || `#${d.doc_id}`).join('、')
}

/* 按小节出题：后台生成（~40s），题目会挂到该节。按钮在任务期间禁用防连点。
   40s 后自动刷一次试题列表 —— quiz_ready 事件把题目送进对话，这里只补侧边栏。 */
const sectionQuiz = ref('')          // 正在出题的小节 id（空 = 空闲）
const sectionQuizError = ref('')

async function quizFromSection(s) {
  if (sectionQuiz.value) return
  sectionQuizError.value = ''
  sectionQuiz.value = s.id
  try {
    await generateSectionQuiz(props.nodeInfo.id, s.id)
    setTimeout(() => {
      sectionQuiz.value = ''
      if (props.visible) loadQuizzes()
    }, 45000)
  } catch (e) {
    sectionQuiz.value = ''
    sectionQuizError.value = formatError(e, { action: '本节出题' })
  }
}

/** 点击题目 → 通知父组件跳到出题页并聚焦该题 */
function openQuiz(q) {
  emit('open-quiz', { nodeId: props.nodeInfo.id, nodeName: props.nodeInfo.name, questionId: q.id })
}

/** 「去出题」→ 通知父组件跳到出题页（只带节点名，不带题 id） */
function openQuizCreator() {
  emit('open-quiz', { nodeId: props.nodeInfo.id, nodeName: props.nodeInfo.name })
}

/* kind 是后端软标签（definition/formula/method/example/mistake/custom…），
   只用于挑图标，未知 kind 落到默认。用内联 lucide 路径（项目禁 emoji，且不引图标库）。 */
const KIND_ICONS = {
  definition: '<path d="M12 7v14"/><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>',
  formula: '<path d="M18 7V4H6l6 8-6 8h12v-3"/>',
  method: '<rect width="16" height="20" x="4" y="2" rx="2"/><line x1="8" x2="16" y1="6" y2="6"/><line x1="16" x2="16" y1="14" y2="18"/><path d="M16 10h.01"/><path d="M12 10h.01"/><path d="M8 10h.01"/><path d="M12 14h.01"/><path d="M8 14h.01"/><path d="M12 18h.01"/><path d="M8 18h.01"/>',
  example: '<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/><path d="M9 18h6"/><path d="M10 22h4"/>',
  mistake: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  _default: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8"/><path d="M16 17H8"/>',
}

/** 侧边栏点击：有 manifest → 懒加载该小节；老节点 → 只有「正文」一项，右侧直接渲染 nodeInfo.content */
function selectDoc(s) {
  if (!hasSections.value) {
    activeSectionId.value = LEGACY_DOC.id
    return
  }
  selectSection(s.id)
}

/** 按 kind 返回内联 SVG 字符串（硬编码常量，无用户输入） */
function kindIconSvg(kind) {
  const inner = KIND_ICONS[kind] || KIND_ICONS._default
  return `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`
}

/**
 * 切换小节：按需请求正文（懒加载）。快速连点时的过期响应会被丢弃。
 * 副作用：更新 activeSectionId / sectionContent / sectionLoading / sectionError。
 */
async function selectSection(id) {
  if (id === activeSectionId.value && sectionContent.value) return
  activeSectionId.value = id
  sectionContent.value = ''
  sectionError.value = ''
  sectionLoading.value = true
  try {
    const { data } = await fetchNodeSection(props.nodeInfo.id, id)
    if (activeSectionId.value !== id) return   // 已被更晚的点击取代
    sectionContent.value = data?.content || ''
  } catch (e) {
    if (activeSectionId.value !== id) return
    sectionError.value = formatError(e, { action: '加载小节' })
  } finally {
    if (activeSectionId.value === id) sectionLoading.value = false
  }
}

/* 面板尺寸 / 正文字号来自设置页（模块级单例，改设置即时生效）。
   阅读模式带左侧栏，面板放宽；编辑模式仍是双栏编辑器，维持原宽。 */
const { prefs: detailPrefs } = useDetailPrefs()
const panelStyle = computed(() => ({
  width: `min(${Math.round((mode.value === 'edit' ? 680 : 880) * detailPrefs.value.scale)}px, 95vw)`,
  maxHeight: `${Math.min(85 * detailPrefs.value.scale, 95)}vh`,
  '--detail-font-size': `${detailPrefs.value.fontSize}px`,
}))

const htmlContent = computed(() => renderMarkdown(props.nodeInfo?.content))
// 编辑模式右侧实时预览（与阅读模式共用同一渲染管线）
const previewHtml = computed(() => renderMarkdown(editContent.value))

watch(() => props.nodeInfo, (val) => {
  if (val) {
    mode.value = 'view'
    editContent.value = val.content || ''
    saveError.value = ''
    // 小节模式：重置本地状态；有则默认载入第一节（懒加载，正文另行请求）
    // 老节点落在「正文」项上（侧边栏恒在，只是只有一项）
    activeSectionId.value = val.has_sections ? '' : LEGACY_DOC.id
    sectionContent.value = ''
    sectionError.value = ''
    // 试题分组：换节点即重置并按需重新拉取（任意节点都渲染该分组）
    quizzes.value = []
    quizzesError.value = ''
    quizzesLoading.value = false
    sectionQuiz.value = ''
    sectionQuizError.value = ''
    loadQuizzes()
    // 来源高亮时优先打开**第一个属于该资料**的小节（否则默认第一节）
    const hit = (val.sections || []).find(s => sectionHasSource(s))
    if (hit) selectSection(hit.id)
    else if (val.has_sections && val.sections?.length) selectSection(val.sections[0].id)
  }
})

// 弹窗开着时在图谱里换了一份高亮资料 → 直接跳到该资料的第一个小节
watch(() => props.sourceDocId, () => {
  const hit = (props.nodeInfo?.sections || []).find(s => sectionHasSource(s))
  if (hit) selectSection(hit.id)
})

function displayName(id) {
  if (!id) return ''
  return id.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

/**
 * 保存节点内容：通过 emit 将保存意图传递给父组件（HomeView），
 * 父组件调用 Store.updateNodeContent() 完成后通过 callback 通知结果。
 *
 * 去耦设计：
 *   - emit('save-content', { nodeId, content, onResult }) —— 传递数据和回调
 *   - 父组件保存成功 → 调用 onResult(null) → 切回 view 模式
 *   - 父组件保存失败 → 调用 onResult(errorMessage) → 显示错误，保持 edit 模式
 */
async function handleSave() {
  saving.value = true
  saveError.value = ''

  // ★ 通过回调让父组件通知保存结果，emit 本身是同步的不会抛异常
  emit('save-content', {
    nodeId: props.nodeInfo.id,
    content: editContent.value,
    onResult: (error) => {
      saving.value = false
      if (error) {
        // 保存失败：保持编辑模式，显示错误
        saveError.value = error
      } else {
        // 保存成功：切回阅读模式，通知父组件刷新
        saveError.value = ''
        mode.value = 'view'
        emit('refresh')
      }
    },
  })
}

/* 掌握程度不再由节点详情手动调整：主信号是出题判分（grade_answer） */
</script>

<template>
  <Transition name="modal">
    <div v-if="visible && nodeInfo" class="modal-overlay" @click.self="emit('close')">
      <div class="modal-panel" :style="panelStyle">
        <!-- 顶部栏 -->
        <div class="modal-header">
          <h2 class="modal-title">{{ nodeInfo.name }}</h2>
          <div class="modal-actions">
            <!-- 阅读/编辑切换（小节模式无单一正文可编辑 → 隐藏入口） -->
            <button
              v-if="mode === 'view' && !hasSections"
              class="action-btn edit-btn"
              @click="mode = 'edit'; editContent = nodeInfo.content || ''"
              title="编辑"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                   stroke-linecap="round" stroke-linejoin="round">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
              </svg>
              编辑
            </button>
            <button v-else-if="mode === 'edit'" class="action-btn save-btn" :disabled="saving" @click="handleSave">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                   stroke-linecap="round" stroke-linejoin="round">
                <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>
                <polyline points="17 21 17 13 7 13 7 21"/>
                <polyline points="7 3 7 8 15 8"/>
              </svg>
              {{ saving ? '保存中...' : '保存' }}
            </button>
            <button v-if="mode === 'edit'" class="action-btn cancel-btn" @click="mode = 'view'">
              取消
            </button>
            <button class="action-btn close-btn" @click="emit('close')" title="关闭">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"
                   stroke-linecap="round">
                <line x1="18" y1="6" x2="6" y2="18"/>
                <line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          </div>
        </div>

        <!-- 编辑模式：Markdown 源码 + 实时预览（分屏） -->
        <div v-if="mode === 'edit'" class="edit-area">
          <textarea
            v-model="editContent"
            class="edit-textarea"
            placeholder="Markdown 格式内容，右侧实时预览..."
          ></textarea>
          <div class="markdown-body edit-preview" v-html="previewHtml"></div>
          <!-- ponytail: 独立成行，故 grid-column 铺满两列 -->
          <p v-if="saveError" class="save-error">{{ saveError }}</p>
        </div>

        <!-- 阅读模式：左侧栏（有 manifest → 小节列表；老节点 → 单项「正文」）+ 右侧正文 -->
        <div v-else-if="mode === 'view'" class="sections-layout">
          <!-- 侧栏外观走设计系统（styles/components.css 的 .sidebar / .sidebar-body /
               .sidebar-nav / .sidebar-item），本组件只补宽度、折叠与状态色 -->
          <aside class="sidebar section-sidebar" :class="{ collapsed: sidebarCollapsed }">
            <!-- 可伸缩：收起后只剩这一条窄边与按钮 -->
            <button
              type="button"
              class="sidebar-toggle"
              :title="sidebarCollapsed ? '展开侧栏' : '收起侧栏'"
              @click="sidebarCollapsed = !sidebarCollapsed"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline v-if="sidebarCollapsed" points="9 18 15 12 9 6"/>
                <polyline v-else points="15 18 9 12 15 6"/>
              </svg>
            </button>
            <div class="sidebar-body section-sidebar-body">
              <ul class="sidebar-nav section-list">
                <li v-for="s in docList" :key="s.id" class="section-row">
                  <button
                    type="button"
                    class="sidebar-item section-item"
                    :class="{ 'is-active': s.id === activeSectionId, 'is-failed': s.status === 'failed',
                              'is-pending': s.status === 'pending', 'is-source-hit': sectionHasSource(s) }"
                    :title="s.title"
                    @click="selectDoc(s)"
                  >
                    <span class="section-kind-icon" v-html="kindIconSvg(s.kind)"></span>
                    <span class="section-title">{{ s.title }}</span>
                  </button>
                  <!-- 按小节出题（老节点的「正文」项没有小节 id，不显示） -->
                  <button
                    v-if="hasSections && s.id !== LEGACY_DOC.id"
                    type="button"
                    class="section-quiz-btn"
                    :disabled="!!sectionQuiz"
                    :title="sectionQuiz === s.id ? '出题中，约 40 秒' : '针对本节出一道题'"
                    @click.stop="quizFromSection(s)"
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                         stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <circle cx="12" cy="12" r="10"/>
                      <path d="M12 8v8"/><path d="M8 12h8"/>
                    </svg>
                  </button>
                </li>
              </ul>
              <p v-if="sectionQuizError" class="save-error section-gen-error">{{ sectionQuizError }}</p>

              <!-- 试题分组（同一侧边栏，小节列表下方）：懒加载，点击跳查出题页 -->
              <div class="quiz-group">
                <div class="sidebar-group-title quiz-group-head">试题</div>
                <p v-if="quizzesLoading" class="section-hint quiz-group-hint">加载中…</p>
                <p v-else-if="quizzesError" class="save-error section-gen-error">{{ quizzesError }}</p>
                <template v-else>
                  <ul v-if="quizzes.length" class="quiz-list">
                    <li v-for="q in quizzes" :key="q.id">
                      <button
                        type="button"
                        class="sidebar-item quiz-item"
                        :class="{ 'is-source-hit': quizHasSource(q) }"
                        :title="q.question"
                        @click="openQuiz(q)"
                      >
                        <span class="quiz-item-main">
                          <span class="quiz-type-badge">{{ quizTypeLabel(q.type) }}</span>
                          <span class="quiz-text">{{ truncateText(q.question) }}</span>
                        </span>
                        <span v-if="q.section_id" class="quiz-node-mark">属 {{ sectionTitle(q.section_id) }}</span>
                        <span v-if="quizDocLabel(q)" class="quiz-doc-mark">来源：{{ quizDocLabel(q) }}</span>
                      </button>
                    </li>
                  </ul>
                  <div v-else class="quiz-empty">
                    <span>该节点还没有试题</span>
                    <button type="button" class="quiz-goto-btn" @click="openQuizCreator">去出题</button>
                  </div>
                </template>
              </div>
            </div>
          </aside>
          <div class="section-content">
            <template v-if="hasSections">
              <!-- 小节级溯源：本节的资料来源（无来源不占位） -->
              <p v-if="activeSources.length" class="section-sources">
                来源：
                <span v-for="(s, i) in activeSources" :key="i">{{ sourceLabel(s) }}<span
                  v-if="i < activeSources.length - 1">、</span></span>
              </p>
              <p v-if="sectionLoading" class="section-hint">加载中…</p>
              <p v-else-if="sectionError" class="save-error section-content-error">{{ sectionError }}</p>
              <div v-else class="markdown-body section-md" v-html="sectionHtml"></div>
            </template>
            <!-- 老节点：无 manifest，右侧直接渲染单 MD（行为不变），侧栏只列「正文」 -->
            <template v-else>
              <div v-if="nodeInfo.content" class="markdown-body section-md" v-html="htmlContent"></div>
              <p v-else class="empty-content">暂无详细内容</p>
            </template>
          </div>
        </div>

        <!-- 前置知识 -->
        <div v-if="nodeInfo.prerequisites?.length" class="relations-section">
          <h3>前置知识</h3>
          <div class="relation-list">
            <button
              v-for="pid in nodeInfo.prerequisites"
              :key="pid"
              class="relation-tag link-btn"
              title="跳转到「{{ displayName(pid) }}」"
              @click="emit('navigate-to-node', pid)"
            >
              ← {{ displayName(pid) }}
            </button>
          </div>
        </div>

        <!-- 相关节点 -->
        <div v-if="nodeInfo.related_nodes?.length" class="relations-section">
          <h3>相关节点</h3>
          <div class="relation-list">
            <button
              v-for="rid in nodeInfo.related_nodes"
              :key="rid"
              class="relation-tag related link-btn"
              title="跳转到「{{ displayName(rid) }}」"
              @click="emit('navigate-to-node', rid)"
            >
              ↔ {{ displayName(rid) }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.modal-overlay {
  position: fixed; inset: 0; z-index: 1000;
  background: rgba(0,0,0,0.5);
  display: flex; align-items: center; justify-content: center;
}

.modal-panel {
  width: min(680px, 95vw); max-height: 85vh;
  background: var(--color-bg-primary); border-radius: 14px;
  display: flex; flex-direction: column;
  box-shadow: var(--shadow-popup);
  overflow: hidden;
  transition: width 0.15s ease, max-height 0.15s ease;
}

.modal-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 18px 24px; border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

/* min-width:0 + ellipsis：窄屏时标题先收缩，别把右侧按钮挤出面板 */
.modal-title { font-size: 18px; font-weight: 700; color: var(--color-text-primary); margin: 0; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.modal-actions { display: flex; align-items: center; gap: 8px; }

.action-btn {
  display: flex; align-items: center; gap: 5px;
  padding: 6px 14px; border: 1px solid var(--color-border);
  border-radius: 8px; background: var(--color-bg-secondary); color: var(--color-text-secondary);
  font-size: 13px; cursor: pointer; transition: all 0.15s;
  white-space: nowrap;
}
.action-btn:hover { background: var(--color-bg-hover); border-color: var(--color-border-light); }
.edit-btn { color: var(--color-accent); border-color: var(--color-accent-light); }
.edit-btn:hover { background: var(--color-accent-light); }
.save-btn { color: var(--color-green); border-color: var(--color-green-light); }
.save-btn:hover { background: var(--color-green-light); }
.save-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.cancel-btn { color: var(--color-text-secondary); }
.close-btn { border: none; background: transparent; color: var(--color-text-muted); padding: 6px; }
.close-btn:hover { background: var(--color-bg-hover); color: var(--color-text-primary); }

/* 可伸缩侧栏：收起后只剩顶部一个箭头按钮 */
.sidebar-toggle {
  display: flex; align-items: center; justify-content: center;
  width: 100%; height: 28px; flex-shrink: 0;
  border: none; border-bottom: 1px solid var(--color-border);
  background: transparent; color: var(--color-text-muted);
  cursor: pointer; transition: background 0.15s, color 0.15s;
}
.sidebar-toggle:hover { background: var(--color-bg-hover); color: var(--color-text-primary); }
.section-sidebar.collapsed { width: 36px; min-width: 36px; }
.section-sidebar.collapsed .section-sidebar-body { display: none; }

/* 编辑区：左源码 / 右实时预览 */
.edit-area {
  flex: 1; min-height: 0; display: grid;
  grid-template-columns: 1fr 1fr; gap: 12px;
  padding: 16px 24px;
}
.edit-textarea {
  width: 100%; height: 100%; min-height: 320px; padding: 14px;
  border: 1px solid var(--color-border); border-radius: 10px;
  font-family: 'Consolas', 'Courier New', monospace;
  font-size: var(--detail-font-size, 14px); line-height: 1.6; resize: vertical;
  color: var(--color-text-primary); background: var(--color-bg-secondary);
}
.edit-textarea:focus { outline: none; border-color: var(--color-accent); }
.edit-preview {
  min-height: 320px; padding: 14px; overflow-y: auto;
  border: 1px solid var(--color-border); border-radius: 10px;
  background: var(--color-bg-secondary);
}
@media (max-width: 760px) {
  .edit-area { grid-template-columns: 1fr; }
}
.save-error { grid-column: 1 / -1; color: var(--color-red); font-size: 13px; margin: 0; }

/* Markdown —— 字号来自设置页（--detail-font-size），标题/代码用相对单位同步缩放 */
.markdown-body {
  flex: 1; overflow-y: auto; padding: 20px 24px;
  font-size: var(--detail-font-size, 14px); line-height: 1.7; color: var(--color-text-primary);
}
.markdown-body :deep(p) { margin: 0 0 10px; }
.markdown-body :deep(strong) { font-weight: 600; }
.markdown-body :deep(code) { background: var(--color-bg-surface); padding: 2px 6px; border-radius: 4px; font-family: 'Consolas', monospace; font-size: 0.93em; }
.markdown-body :deep(pre) { background: var(--color-bg-tertiary); color: var(--color-text-primary); padding: 14px 16px; border-radius: 10px; overflow-x: auto; margin: 12px 0; font-size: 0.93em; }
.markdown-body :deep(pre code) { background: transparent; padding: 0; color: inherit; }
.markdown-body :deep(h1), .markdown-body :deep(h2), .markdown-body :deep(h3) { margin: 18px 0 8px; font-weight: 600; color: var(--color-text-primary); }
.markdown-body :deep(h1) { font-size: 1.43em; }
.markdown-body :deep(h2) { font-size: 1.21em; }
.markdown-body :deep(blockquote) { border-left: 3px solid var(--color-accent); padding-left: 14px; margin: 12px 0; color: var(--color-text-secondary); }
.markdown-body :deep(ul), .markdown-body :deep(ol) { padding-left: 20px; margin: 8px 0; }
.empty-content { color: var(--color-text-muted); font-style: italic; text-align: center; padding: 40px 0; }

/* 小节模式：左列表 / 右正文。
   侧栏的壳 / 滚动区 / 列表 / 列表项外观全部来自设计系统（styles/components.css 的
   .sidebar / .sidebar-body / .sidebar-nav / .sidebar-item），这里只留本弹窗特有的
   宽度、折叠与状态色 —— 别再抄一份背景色/内边距/圆角。 */
.sections-layout { flex: 1; min-height: 0; display: flex; }
.section-sidebar { width: 190px; min-width: 190px; flex-shrink: 0; }
.section-gen-error { margin: 8px 0 0; font-size: 12px; }
/* .sidebar-body 已给弹性/滚动/内边距，这里补纵向排列（试题分组要接在小节列表下方） */
.section-sidebar-body { display: flex; flex-direction: column; }
/* .sidebar-nav 已给纵向列表与间距；ul 自身样式要清掉（含内边距，避免与外层叠成双份） */
.section-list { list-style: none; margin: 0; padding: 0; }
/* 小节行 = 小节按钮 + 「针对本节出题」按钮（行内 flex，按钮不挤走标题） */
.section-row { display: flex; align-items: center; gap: 2px; }
/* 外观来自 .sidebar-item，这里只让按钮在小节行里占满剩余宽度 */
.section-row .section-item { flex: 1; min-width: 0; }
.section-quiz-btn {
  flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center;
  width: 24px; height: 24px; padding: 0; border: none; border-radius: 6px;
  background: transparent; color: var(--color-text-muted); cursor: pointer;
  opacity: 0.55; transition: all 0.15s;
}
.section-quiz-btn:hover:not(:disabled) { background: var(--color-bg-hover); color: var(--color-accent); opacity: 1; }
.section-quiz-btn:disabled { cursor: not-allowed; opacity: 0.3; }
/* 状态色覆盖 .sidebar-item 的默认/激活外观（hover 与 is-active 由设计系统提供） */
.section-item.is-failed { color: var(--color-red); }        /* 生成失败：警示色 */
.section-item.is-pending { color: var(--color-text-muted); } /* 待生成：次要色 */
/* 来源高亮（「在图谱中显示」命中的资料）—— 放在 .is-active 之后，两者同时命中时以它为准 */
.section-item.is-source-hit { background: var(--color-green-light); color: var(--color-green); font-weight: 600; }
.section-kind-icon { flex-shrink: 0; display: inline-flex; }
.section-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 试题分组（同侧边栏，小节列表下方） */
.quiz-group { border-top: 1px solid var(--color-border); padding: 8px; }
.quiz-group-hint { padding: 2px; margin: 0; font-size: 12px; }
.quiz-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
/* 紧凑变体：外观（内边距 / 圆角 / hover）沿用 .sidebar-item，只把密度压小 ——
   一行要放题型徽标 + 题干 + 来源，按 .sidebar-item 的 14px 字号会挤掉题干 */
.quiz-item {
  flex-direction: column;
  align-items: stretch;
  gap: 3px;
  padding: var(--space-xs) var(--space-sm);
  font-size: var(--font-size-sm);
}
.quiz-item.is-source-hit { background: var(--color-green-light); }
.quiz-item-main { display: flex; align-items: center; gap: 6px; min-width: 0; }
.quiz-type-badge {
  flex-shrink: 0; padding: 1px 6px; border-radius: 8px; font-size: 10px; font-weight: 600;
  background: var(--color-accent-light); color: var(--color-accent);
}
.quiz-text { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.quiz-node-mark { font-size: 10px; color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.quiz-doc-mark { font-size: 10px; color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.quiz-empty { display: flex; flex-direction: column; gap: 6px; padding: 4px 2px; font-size: 12px; color: var(--color-text-muted); }
.quiz-goto-btn {
  align-self: flex-start; padding: 4px 10px; border: 1px solid var(--color-border);
  border-radius: 8px; background: var(--color-bg-secondary); color: var(--color-text-secondary);
  font-size: 12px; cursor: pointer; transition: all 0.15s;
}
.quiz-goto-btn:hover { background: var(--color-bg-hover); border-color: var(--color-border-light); color: var(--color-text-primary); }

.section-content { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.section-md { flex: 1; }
/* 小节级溯源：正文上方一行浅色来源标注 */
.section-sources {
  margin: 0; padding: 8px 24px; flex-shrink: 0;
  font-size: 12px; color: var(--color-text-muted);
  border-bottom: 1px solid var(--color-border);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.section-hint { padding: 16px 24px; margin: 0; font-size: 13px; color: var(--color-text-muted); }
.section-content-error { padding: 16px 24px; margin: 0; font-size: 13px; }
@media (max-width: 560px) { .section-sidebar { width: 140px; min-width: 140px; } }

/* 关系 */
.relations-section { padding: 14px 24px 18px; border-top: 1px solid var(--color-border); flex-shrink: 0; }
.relations-section h3 { font-size: 13px; font-weight: 600; color: var(--color-text-secondary); text-transform: uppercase; letter-spacing: 0.5px; margin: 0 0 8px; }
.relation-list { display: flex; flex-wrap: wrap; gap: 6px; }
.relation-tag { padding: 3px 10px; border-radius: 6px; font-size: 12px; background: var(--color-blue-light); color: var(--color-blue); border: 1px solid var(--color-border-light); }
.relation-tag.related { background: var(--color-accent-light); color: var(--color-purple); border-color: var(--color-accent-light); }
.relation-tag.link-btn {
  cursor: pointer;
  transition: all 0.15s;
  font-family: inherit;
}
.relation-tag.link-btn:hover {
  filter: brightness(0.95);
  transform: translateY(-1px);
}

/* 弹窗动画 */
.modal-enter-active, .modal-leave-active { transition: opacity 0.2s ease; }
.modal-enter-from, .modal-leave-to { opacity: 0; }
.modal-enter-from .modal-panel, .modal-leave-to .modal-panel { transform: scale(0.95); }
</style>
