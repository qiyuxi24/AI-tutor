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
import { fetchNodeSection, fetchNodeQuizzes } from '../api/kb.js'
import { formatError } from '../utils/errorCodes.js'
import { useDetailPrefs } from '../utils/detailPrefs.js'

const props = defineProps({
  nodeInfo: { type: Object, default: null },
  visible: { type: Boolean, default: false },
})

const emit = defineEmits(['close', 'refresh', 'save-content', 'navigate-to-node', 'update-mastery', 'open-quiz'])

const mode = ref('view')        // 'view' | 'edit'
const editContent = ref('')
const saving = ref(false)
const saveError = ref('')
const masterySlider = ref(0)
const masterySaving = ref(false)

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
    masterySlider.value = val.mastery || 0
    // 小节模式：重置本地状态；有则默认载入第一节（懒加载，正文另行请求）
    // 老节点落在「正文」项上（侧边栏恒在，只是只有一项）
    activeSectionId.value = val.has_sections ? '' : LEGACY_DOC.id
    sectionContent.value = ''
    sectionError.value = ''
    // 试题分组：换节点即重置并按需重新拉取（任意节点都渲染该分组）
    quizzes.value = []
    quizzesError.value = ''
    quizzesLoading.value = false
    loadQuizzes()
    if (val.has_sections && val.sections?.length) selectSection(val.sections[0].id)
  }
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

/* 掌握程度映射 */
function masteryLabel(m) {
  if (m == null || m === 0) return '未掌握'
  if (m <= 25) return '入门'
  if (m <= 50) return '熟悉'
  if (m <= 75) return '熟练'
  return '精通'
}
function masteryBarWidth(m) {
  return Math.min(100, Math.max(0, m || 0)) + '%'
}

async function handleMasteryChange() {
  masterySaving.value = true
  emit('update-mastery', {
    nodeId: props.nodeInfo.id,
    mastery: masterySlider.value,
    onResult: (error) => {
      masterySaving.value = false
      if (!error) {
        emit('refresh')
      }
    },
  })
}
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

        <!-- 掌握程度条 + 滑块调整 -->
        <div class="mastery-section">
          <div class="mastery-label">
            <span>掌握程度</span>
            <span class="mastery-value">{{ masteryLabel(masterySlider) }}{{ masterySaving ? ' (保存中...)' : '' }}</span>
          </div>
          <div class="mastery-bar-bg">
            <div class="mastery-bar-fill" :style="{ width: masteryBarWidth(masterySlider) }"></div>
          </div>
          <input
            type="range"
            min="0"
            max="100"
            step="1"
            :value="masterySlider"
            @input="masterySlider = Number($event.target.value)"
            @change="handleMasteryChange"
            class="mastery-slider"
            :disabled="masterySaving"
            title="拖动调整掌握程度"
          />
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
          <aside class="section-sidebar" :class="{ collapsed: sidebarCollapsed }">
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
            <div class="section-sidebar-body">
              <ul class="section-list">
                <li v-for="s in docList" :key="s.id">
                  <button
                    type="button"
                    class="section-item"
                    :class="{ active: s.id === activeSectionId, 'is-failed': s.status === 'failed', 'is-pending': s.status === 'pending' }"
                    :title="s.title"
                    @click="selectDoc(s)"
                  >
                    <span class="section-kind-icon" v-html="kindIconSvg(s.kind)"></span>
                    <span class="section-title">{{ s.title }}</span>
                  </button>
                </li>
              </ul>

              <!-- 试题分组（同一侧边栏，小节列表下方）：懒加载，点击跳查出题页 -->
              <div class="quiz-group">
                <div class="quiz-group-head">试题</div>
                <p v-if="quizzesLoading" class="section-hint quiz-group-hint">加载中…</p>
                <p v-else-if="quizzesError" class="save-error section-gen-error">{{ quizzesError }}</p>
                <template v-else>
                  <ul v-if="quizzes.length" class="quiz-list">
                    <li v-for="q in quizzes" :key="q.id">
                      <button
                        type="button"
                        class="quiz-item"
                        :title="q.question"
                        @click="openQuiz(q)"
                      >
                        <span class="quiz-item-main">
                          <span class="quiz-type-badge">{{ quizTypeLabel(q.type) }}</span>
                          <span class="quiz-text">{{ truncateText(q.question) }}</span>
                        </span>
                        <span v-if="q.section_id" class="quiz-node-mark">属 {{ sectionTitle(q.section_id) }}</span>
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

/* 掌握程度条 */
.mastery-section { padding: 12px 24px; background: var(--color-bg-surface); flex-shrink: 0; }
.mastery-label { display: flex; justify-content: space-between; font-size: 12px; color: var(--color-text-secondary); margin-bottom: 5px; }
.mastery-value { color: var(--color-green); font-weight: 600; }
.mastery-bar-bg { height: 6px; background: var(--color-border); border-radius: 3px; overflow: hidden; }
.mastery-bar-fill { height: 100%; background: linear-gradient(90deg, var(--color-green-light), var(--color-green)); border-radius: 3px; transition: width 0.3s ease; }

.mastery-slider {
  width: 100%; margin-top: 8px; height: 4px;
  -webkit-appearance: none; appearance: none;
  background: var(--color-border); border-radius: 2px; outline: none; cursor: pointer;
}
.mastery-slider::-webkit-slider-thumb {
  -webkit-appearance: none; appearance: none;
  width: 14px; height: 14px; border-radius: 50%;
  background: var(--color-green); cursor: pointer; border: 2px solid var(--color-bg-primary);
  box-shadow: 0 1px 3px rgba(0,0,0,0.2);
}
.mastery-slider::-moz-range-thumb {
  width: 14px; height: 14px; border-radius: 50%;
  background: var(--color-green); cursor: pointer; border: 2px solid var(--color-bg-primary);
  box-shadow: 0 1px 3px rgba(0,0,0,0.2);
}
.mastery-slider:disabled { opacity: 0.5; cursor: not-allowed; }

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

/* 小节模式：左列表 / 右正文（沿用面板风格与既有配色变量） */
.sections-layout { flex: 1; min-height: 0; display: flex; }
.section-sidebar {
  width: 190px; min-width: 190px; flex-shrink: 0;
  display: flex; flex-direction: column;
  border-right: 1px solid var(--color-border); background: var(--color-bg-surface);
}
.section-gen-error { margin: 8px 0 0; font-size: 12px; }
.section-sidebar-body { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; }
.section-list {
  list-style: none; margin: 0; padding: 8px;
  display: flex; flex-direction: column; gap: 2px;
}
.section-item {
  display: flex; align-items: center; gap: 8px; width: 100%;
  padding: 8px 10px; border: none; border-radius: 8px; background: transparent;
  color: var(--color-text-secondary); font-size: 13px; text-align: left; cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.section-item:hover { background: var(--color-bg-hover); color: var(--color-text-primary); }
.section-item.active { background: var(--color-accent-light); color: var(--color-accent); font-weight: 600; }
.section-item.is-failed { color: var(--color-red); }        /* 生成失败：警示色 */
.section-item.is-pending { color: var(--color-text-muted); } /* 待生成：次要色 */
.section-kind-icon { flex-shrink: 0; display: inline-flex; }
.section-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 试题分组（同侧边栏，小节列表下方） */
.quiz-group { border-top: 1px solid var(--color-border); padding: 8px; }
.quiz-group-head { font-size: 12px; font-weight: 600; color: var(--color-text-secondary); text-transform: uppercase; letter-spacing: 0.5px; margin: 0 0 6px; padding: 0 2px; }
.quiz-group-hint { padding: 2px; margin: 0; font-size: 12px; }
.quiz-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
.quiz-item {
  display: flex; flex-direction: column; gap: 3px; width: 100%;
  padding: 7px 8px; border: none; border-radius: 8px; background: transparent;
  color: var(--color-text-secondary); font-size: 12px; text-align: left; cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.quiz-item:hover { background: var(--color-bg-hover); color: var(--color-text-primary); }
.quiz-item-main { display: flex; align-items: center; gap: 6px; min-width: 0; }
.quiz-type-badge {
  flex-shrink: 0; padding: 1px 6px; border-radius: 8px; font-size: 10px; font-weight: 600;
  background: var(--color-accent-light); color: var(--color-accent);
}
.quiz-text { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.quiz-node-mark { font-size: 10px; color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.quiz-empty { display: flex; flex-direction: column; gap: 6px; padding: 4px 2px; font-size: 12px; color: var(--color-text-muted); }
.quiz-goto-btn {
  align-self: flex-start; padding: 4px 10px; border: 1px solid var(--color-border);
  border-radius: 8px; background: var(--color-bg-secondary); color: var(--color-text-secondary);
  font-size: 12px; cursor: pointer; transition: all 0.15s;
}
.quiz-goto-btn:hover { background: var(--color-bg-hover); border-color: var(--color-border-light); color: var(--color-text-primary); }

.section-content { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.section-md { flex: 1; }
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
