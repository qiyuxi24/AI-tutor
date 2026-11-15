<script setup>
/**
 * NodeDetail.vue — 节点详情弹窗
 *
 * 职责：展示节点详情 + 阅读/编辑正文（老节点单 MD / 小节化节点的某一节）。
 *
 * 正文的阅读与编辑**不再由本组件实现**：交给 `DocWorkspace`（与知识库页共用同一个组件，
 * 也是全站唯一的 Markdown 编辑入口，编辑器为开源 Vditor）。本组件只负责：
 *   · 左侧栏（小节列表 + 试题分组）与「哪一节被选中」；
 *   · 把「当前这节的正文」包装成 DocWorkspace 要的 doc 对象 + 三个适配器；
 *   · 节点特有的东西：去学习 / 前置知识 / 相关节点 / 小节学习状态条。
 *
 * 为什么小节也要能编辑：小节化（2026-09-27）之后正文在多个平行 MD 里，没有主文件，
 * 于是旧的「编辑」按钮对这类节点直接隐藏 —— 结果是**大部分节点根本没编辑入口**。
 * 2026-11-15 补上 `PUT /knowledge/node/{id}/section/{sid}` 后两处口径一致。
 *
 * 去耦设计（保留）：
 *   - 老节点的正文保存仍通过 emit('save-content', { nodeId, content, onResult }) 交给父组件
 *     （父组件走 Store → 刷新图谱）；小节正文直接走 `saveNodeSection`（与 fetchNodeSection 同层，
 *     都是 kb.js 的封装，不裸调 apiClient）。
 *   - 组件不 import store。
 *
 * Props:
 *   nodeInfo: Object  — 节点详情数据
 *   visible: Boolean  — 弹窗显示状态
 *   sourceDocId: Number|String — 当前在图谱里高亮的资料 doc_id（命中来源的小节/题目加醒目样式）
 *
 * Emits:
 *   close: ()                  — 关闭弹窗
 *   refresh: ()                — 请求父组件刷新数据
 *   save-content: ({ nodeId, content, onResult }) — 保存老节点单 MD（父组件调用 Store）
 *   navigate-to-node: (nodeId) — 跳到另一个知识点
 *   open-quiz: ({...})         — 去出题页
 *   go-learn: (nodeId)         — 带着这个节点去对话里学习
 *   open-lecture: ({ docId, at }) — 从「视频出处」跳到课时页（父组件负责切视图）
 */

import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
// ★ 保存操作由父组件通过 Store 处理；此处只读小节正文/试题列表与触发生成（走 kb.js 封装，不裸调 apiClient）
import { fetchNodeSection, fetchNodeQuizzes, generateSectionQuiz, saveNodeSection, updateSectionLearn } from '../api/kb.js'
import { formatError } from '../utils/errorCodes.js'
import { useDetailPrefs } from '../utils/detailPrefs.js'
import DocWorkspace from './DocWorkspace.vue'
import VideoSourceCard from './VideoSourceCard.vue'

const props = defineProps({
  nodeInfo: { type: Object, default: null },
  visible: { type: Boolean, default: false },
  // 来源高亮：当前在图谱里高亮的资料 doc_id（来自知识库右键「在图谱中显示」）。
  // 非空时，属于这份资料的小节与题目会加醒目样式，并自动打开第一个命中小节。
  sourceDocId: { type: [Number, String], default: null },
})

const emit = defineEmits(['close', 'refresh', 'save-content', 'navigate-to-node', 'open-quiz', 'go-learn', 'open-lecture'])

/** 全屏：把模态撑满视口（阅读长讲义/写长文时的唯一诉求，不换路由也不换组件） */
const fullscreen = ref(false)

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

/* ── 正文交给共用工作区（DocWorkspace）──
   本组件不再渲染正文/编辑器，只回答三个问题：现在该看哪一节、正文从哪读、存到哪。
   这两组适配器就是"知识节点小节"与"知识库文件"的全部差别（见 DocWorkspace 的注释）。 */
const workspaceRef = ref(null)

/** 当前要展示的文档：有小节 = 选中那一节；老节点 = 唯一那篇「正文」 */
const wsDoc = computed(() => {
  const info = props.nodeInfo
  if (!info) return null
  if (!hasSections.value) {
    return { id: info.id, nodeId: info.id, sectionId: '', name: '正文', legacy: true }
  }
  if (!activeSectionId.value) return null
  const s = sections.value.find(x => x.id === activeSectionId.value)
  return {
    // id 参与工作区的 watch：换节即重读（同一节点的不同小节必须是不同 id）
    id: `${info.id}:${activeSectionId.value}`,
    nodeId: info.id,
    sectionId: activeSectionId.value,
    name: s?.title || activeSectionId.value,
  }
})

/** 读正文：小节走 GET .../section/{sid}（懒加载，点开哪节读哪节）；老节点直接用详情里的 content */
async function wsLoadText(doc) {
  if (doc.legacy) {
    return { text: props.nodeInfo?.content || '', editable: true }
  }
  const { data } = await fetchNodeSection(doc.nodeId, doc.sectionId)
  return {
    text: data?.content || '',
    // 小节一律可编辑（后端 write_section 无格式限制，正文就是这节 MD 全文）
    editable: true,
  }
}

/**
 * 存正文。
 * · 小节：直接 PUT 该节（写库 + 标 filled + 重索引都在后端一次做完），成功后通知父级刷新；
 * · 老节点：仍走 emit('save-content') —— 父组件经 Store 保存并刷新图谱，这条链路没变。
 * 失败**必须抛**：DocWorkspace 靠捕获异常把 detail 显示在编辑区。
 */
async function wsSaveText(doc, text) {
  if (doc.legacy) {
    await new Promise((resolve, reject) => {
      emit('save-content', {
        nodeId: doc.nodeId,
        content: text,
        onResult: (error) => (error ? reject(new Error(error)) : resolve()),
      })
    })
    return
  }
  await saveNodeSection(doc.nodeId, doc.sectionId, text)
  // 节点详情的 updated_at / content_status 变了 → 让父级重拉（图谱与看板跟着新）
  emit('refresh')
}

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

/* ── 小节学习状态（已懂 / 不懂 / 已读完 / 出题通过）──
   数据源：`nodeInfo.sections[].learn`（后端 manifest 的 learn 子对象）。
   `mark_by` 记“谁标的”：user = 学生自己点的，ai = AI 讲完自动记的（工具 mark_section_understood）。
   本地**乐观更新**：点一下立刻改 UI，不整页重拉；父组件重拉后自动以服务端为准。 */
const DEFAULT_LEARN = { mark: 'unknown', read: false, passed: false, attempts: 0, mark_by: '' }
const localLearn = ref({})        // section_id → learn（本次会话内的乐观值）
const learnBusy = ref('')
const learnError = ref('')

watch(() => props.nodeInfo, () => { localLearn.value = {}; learnError.value = '' })

/** 某小节的学习状态（服务端值 ← 本地乐观值覆盖） */
function learnOf(s) {
  return { ...DEFAULT_LEARN, ...(s.learn || {}), ...(localLearn.value[s.id] || {}) }
}

const activeLearn = computed(() => {
  const s = sections.value.find(x => x.id === activeSectionId.value)
  return s ? learnOf(s) : DEFAULT_LEARN
})

/** 已通过 / 总节数（本地统计 → 标记后立即变） */
const learnProgress = computed(() => {
  const list = sections.value
  if (!list.length) return null
  return { total: list.length, passed: list.filter(s => learnOf(s).passed).length }
})

/** 小节列表上的状态小标签（空文本 = 未标记，不占位）
    「已懂」「不懂」都要区分**谁标的**：AI 讲完自动记的与你自己点的是两回事 */
function learnTag(s) {
  const st = learnOf(s)
  if (st.passed) return { text: '通过', cls: 'lp-passed', title: '已通过出题验证' }
  if (st.mark === 'understood') {
    return st.mark_by === 'ai'
      ? { text: '已懂', cls: 'lp-understood lp-ai', title: 'AI 讲完后自动标记，需出题答对才算通过' }
      : { text: '已懂', cls: 'lp-understood', title: '已标记，进对话时先出题验证' }
  }
  if (st.mark === 'confused') {
    return st.mark_by === 'ai'
      ? { text: '不懂', cls: 'lp-confused lp-ai', title: '连续两次答错，系统已自动标记' }
      : { text: '不懂', cls: 'lp-confused', title: '已标记，进对话时从这里讲' }
  }
  if (st.read) return { text: '已读', cls: 'lp-read', title: '已标记为读完' }
  return { text: '', cls: 'lp-none', title: '未标记' }
}

/** 标记小节状态（已懂 / 不懂 / 已读完）；全部通过时通知父组件刷图谱 */
async function markLearn(sectionId, patch) {
  if (!sectionId || sectionId === LEGACY_DOC.id || learnBusy.value) return
  learnBusy.value = sectionId
  learnError.value = ''
  try {
    const { data } = await updateSectionLearn(props.nodeInfo.id, sectionId, patch)
    localLearn.value = { ...localLearn.value, [sectionId]: data.learn || {} }
    // 掌握度被后端改了（全部小节通过 → 置 100）：通知父组件刷新详情与图谱
    if (data.mastery !== undefined && data.mastery !== props.nodeInfo.mastery) {
      emit('refresh')
    }
  } catch (e) {
    learnError.value = formatError(e)
  } finally {
    learnBusy.value = ''
  }
}

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

/** 侧边栏点击：有 manifest → 切到该小节；老节点 → 只有「正文」一项（正文已在详情里） */
function selectDoc(s) {
  const nextId = hasSections.value ? s.id : LEGACY_DOC.id
  if (nextId === activeSectionId.value) return
  // 未保存的修改不能因为点一下左侧就没了（判断在 DocWorkspace 里）
  if (workspaceRef.value?.canLeave?.() === false) return
  activeSectionId.value = nextId
}

/** 按 kind 返回内联 SVG 字符串（硬编码常量，无用户输入） */
function kindIconSvg(kind) {
  const inner = KIND_ICONS[kind] || KIND_ICONS._default
  return `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`
}

/* 面板尺寸 / 正文字号来自设置页（模块级单例，改设置即时生效）。
   全屏时不参与尺寸计算（inset:0 铺满），阅读模式带左侧栏故比编辑态宽。 */
const { prefs: detailPrefs } = useDetailPrefs()
const panelStyle = computed(() => {
  if (fullscreen.value) {
    return {
      width: '100vw',
      maxWidth: '100vw',
      height: '100vh',
      maxHeight: '100vh',
      borderRadius: '0',
      '--detail-font-size': `${detailPrefs.value.fontSize}px`,
    }
  }
  return {
    width: `min(${Math.round(880 * detailPrefs.value.scale)}px, 95vw)`,
    maxHeight: `${Math.min(85 * detailPrefs.value.scale, 95)}vh`,
    '--detail-font-size': `${detailPrefs.value.fontSize}px`,
  }
})

/* 换节点 / 数据刷新：`nodeInfo` 是父级每次 fetch 都新建的对象，
   **保存小节后 emit('refresh') 也会走这里** —— 所以不能无条件重置，
   否则用户改完第 3 节、保存，视图会跳回第 1 节（真实 bug，2026-11-15 发现）。

   判据：节点 id 变了才算"换节点"，才回到默认小节；同节点刷新只保留当前小节
   （若它已被删掉，才回落到第一节）。 */
watch(() => props.nodeInfo, (val, old) => {
  if (!val) return
  const sameNode = old && old.id === val.id
  if (!sameNode) fullscreen.value = false

  // 试题分组：换节点才重置并重拉（同节点刷新不动，免得每次保存都闪一下）
  if (!sameNode) {
    quizzes.value = []
    quizzesError.value = ''
    quizzesLoading.value = false
    sectionQuiz.value = ''
    sectionQuizError.value = ''
    loadQuizzes()
  }

  if (!val.has_sections) {
    activeSectionId.value = LEGACY_DOC.id
    return
  }

  const ids = (val.sections || []).map(s => s.id)
  // 当前小节还在就留着（刷新不该挪动用户的位置）
  if (sameNode && ids.includes(activeSectionId.value)) return

  // 来源高亮时优先打开**第一个属于该资料**的小节（否则默认第一节）
  const hit = (val.sections || []).find(s => sectionHasSource(s))
  if (hit) activeSectionId.value = hit.id
  else if (ids.length) activeSectionId.value = ids[0]
  else activeSectionId.value = ''
})

// 弹窗开着时在图谱里换了一份高亮资料 → 直接跳到该资料的第一个小节
watch(() => props.sourceDocId, () => {
  const hit = (props.nodeInfo?.sections || []).find(s => sectionHasSource(s))
  if (hit) activeSectionId.value = hit.id
})

/* Esc 关闭弹窗：window 上挂一次即可（不用 watch(visible) 反复绑/解绑）。
   只在 visible 为真时响应，弹窗关着时不抢别的组件的 Esc。
   全屏时 Esc 先退全屏（一次 Esc 直接关掉正在读的长文太粗暴）。 */
function handleKeydown(e) {
  if (e.key !== 'Escape' || !props.visible) return
  // 编辑器自己也会用 Esc（退出全屏编辑/关弹层），正在编辑时让给它
  if (document.activeElement?.closest?.('.vditor')) return
  if (fullscreen.value) {
    fullscreen.value = false
    return
  }
  emit('close')
}
onMounted(() => window.addEventListener('keydown', handleKeydown))
onUnmounted(() => window.removeEventListener('keydown', handleKeydown))

function displayName(id) {
  if (!id) return ''
  return id.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
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
            <!-- 去学习：切到对话视图，后端按小节教学（见 chat_service._build_learn_block） -->
            <button
              class="action-btn learn-btn"
              title="按小节带你过一遍这个知识点"
              @click="emit('go-learn', nodeInfo.id)"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                   stroke-linecap="round" stroke-linejoin="round">
                <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>
                <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
              </svg>
              去学习
            </button>
            <!-- 全屏：读长讲义/写长文的唯一诉求。只改面板尺寸，不换路由也不换组件 -->
            <button
              class="action-btn"
              :title="fullscreen ? '退出全屏（Esc）' : '全屏阅读'"
              @click="fullscreen = !fullscreen"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                   stroke-linecap="round" stroke-linejoin="round">
                <template v-if="fullscreen">
                  <polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/>
                  <line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/>
                </template>
                <template v-else>
                  <polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/>
                  <line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>
                </template>
              </svg>
              {{ fullscreen ? '退出全屏' : '全屏' }}
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

        <!-- 主体：左侧栏（有 manifest → 小节列表；老节点 → 单项「正文」）+ 右侧共用工作区 -->
        <div class="sections-layout">
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
                    <!-- 学习状态：通过 > 已懂 > 不懂 > 已读（未标记不占位） -->
                    <span
                      v-if="hasSections && s.id !== LEGACY_DOC.id && learnTag(s).text"
                      class="section-learn-tag"
                      :class="learnTag(s).cls"
                      :title="learnTag(s).title"
                    >{{ learnTag(s).text }}</span>
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
                    <span>还没有试题</span>
                    <button type="button" class="quiz-goto-btn" @click="openQuizCreator">去出题</button>
                  </div>
                </template>
              </div>
            </div>
          </aside>
          <div class="section-content">
            <template v-if="hasSections">
              <!-- 学习状态操作条：用户自己标记“这一节我懂不懂 / 读完了没”。
                   这里是**用户**的入口；AI 讲完一节也会自动记「已懂」（mark_by='ai'，虚线描边区分）。
                   已懂 ≠ 通过 —— 进对话后会先出题验证（后端口径，见 section_learn）。 -->
              <div v-if="activeSectionId && activeSectionId !== LEGACY_DOC.id" class="section-learn-bar">
                <span class="slb-label">这一节：</span>
                <button
                  type="button" class="slb-btn"
                  :class="{ on: activeLearn.mark === 'understood' }"
                  :disabled="learnBusy === activeSectionId"
                  title="标记为已懂，进对话时先出题验证"
                  @click="markLearn(activeSectionId, { mark: 'understood' })"
                >已懂</button>
                <button
                  type="button" class="slb-btn"
                  :class="{ on: activeLearn.mark === 'confused' }"
                  :disabled="learnBusy === activeSectionId"
                  title="标记为不懂，去学习时从这里开始讲"
                  @click="markLearn(activeSectionId, { mark: 'confused' })"
                >不懂</button>
                <label class="slb-read">
                  <input
                    type="checkbox"
                    :checked="activeLearn.read"
                    :disabled="learnBusy === activeSectionId"
                    @change="markLearn(activeSectionId, { read: $event.target.checked })"
                  >
                  已读完
                </label>
                <span v-if="activeLearn.passed" class="slb-passed">✓ 已通过</span>
                <span
                  v-else-if="activeLearn.mark === 'understood' && activeLearn.mark_by === 'ai'"
                  class="slb-ai-mark"
                  title="AI 讲完后自动标记，需出题答对才算通过"
                >待验证</span>
                <span v-if="learnProgress" class="slb-progress">
                  {{ learnProgress.passed }}/{{ learnProgress.total }} 节
                </span>
              </div>
              <p v-if="learnError" class="save-error section-gen-error">{{ learnError }}</p>
              <!-- 小节级溯源：本节的资料来源（无来源不占位） -->
              <p v-if="activeSources.length" class="section-sources">
                来源：
                <span v-for="(s, i) in activeSources" :key="i">{{ sourceLabel(s) }}<span
                  v-if="i < activeSources.length - 1">、</span></span>
              </p>
            </template>

            <!-- 视频出处：这个知识点在课堂视频的哪一分钟讲过（反查 /knowledge/node/{id}/video-sources）。
                 放在小节溯源之后、正文之前 —— 溯源信息聚在一起，读正文前先知道"这话从哪来"。
                 无视频出处的节点**整卡不渲染**（组件内 v-if），所以绝大多数节点看不到它，
                 不会凭空多出一条空区块。 -->
            <VideoSourceCard
              v-if="nodeInfo?.id"
              :node-id="String(nodeInfo.id)"
              @open-lecture="emit('open-lecture', $event)"
            />

            <!-- 正文：阅读/编辑都交给共用工作区（与知识库页同一个组件、同一个编辑器）。
                 节点侧只提供三个适配器：读这节正文 / 存这节正文 / 没有原件。 -->
            <DocWorkspace
              ref="workspaceRef"
              bare
              :doc="wsDoc"
              :load-text="wsLoadText"
              :save-text="wsSaveText"
              empty-title="这个节点还没有正文"
              empty-hint="可以让 AI 在对话里补全，或让它重新做一次小节化"
            />
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
              :title="`跳转到「${displayName(pid)}」`"
              @click="emit('navigate-to-node', pid)"
            >
              ← {{ displayName(pid) }}
            </button>
          </div>
        </div>

        <!-- 相关知识点 -->
        <div v-if="nodeInfo.related_nodes?.length" class="relations-section">
          <h3>相关知识点</h3>
          <div class="relation-list">
            <button
              v-for="rid in nodeInfo.related_nodes"
              :key="rid"
              class="relation-tag related link-btn"
              :title="`跳转到「${displayName(rid)}」`"
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
  background: var(--color-bg-overlay);
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

/* 正文区：内部是 DocWorkspace（阅读 + 编辑），这里只给它一个能撑满的高度容器。
   排版样式（.markdown-body）原先由本组件维护，已随编辑器一起搬进 DocWorkspace —— 
   两个组件各写一套 Markdown 排版正是这次要消除的重复。 */
.section-content {
  flex: 1; min-width: 0; min-height: 0;
  display: flex; flex-direction: column;
}
.save-error { color: var(--color-red); font-size: 13px; margin: 0; }

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
  flex-shrink: 0; padding: 1px 6px; border-radius: 8px; font-size: 11px; font-weight: 600;
  background: var(--color-accent-light); color: var(--color-accent);
}
.quiz-text { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.quiz-node-mark { font-size: 11px; color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.quiz-doc-mark { font-size: 11px; color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.quiz-empty { display: flex; flex-direction: column; gap: 6px; padding: 4px 2px; font-size: 12px; color: var(--color-text-muted); }
.quiz-goto-btn {
  align-self: flex-start; padding: 4px 10px; border: 1px solid var(--color-border);
  border-radius: 8px; background: var(--color-bg-secondary); color: var(--color-text-secondary);
  font-size: 12px; cursor: pointer; transition: all 0.15s;
}
.quiz-goto-btn:hover { background: var(--color-bg-hover); border-color: var(--color-border-light); color: var(--color-text-primary); }

/* 小节级溯源：正文上方一行浅色来源标注 */
.section-sources {
  margin: 0; padding: 8px 24px; flex-shrink: 0;
  font-size: 12px; color: var(--color-text-muted);
  border-bottom: 1px solid var(--color-border);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.section-hint { padding: 16px 24px; margin: 0; font-size: 13px; color: var(--color-text-muted); }
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
/* ── 小节学习状态（用户标记：已懂 / 不懂 / 已读完）── */
.section-learn-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 6px 9px;
  margin-bottom: 8px;
  border: 1px solid var(--color-border-default, #ddd);
  border-radius: 6px;
  background: var(--color-bg-tertiary, rgba(127, 127, 127, 0.06));
  font-size: 12px;
}
.slb-label { color: var(--color-text-tertiary); }
.slb-btn {
  font-family: inherit;
  font-size: 12px;
  padding: 2px 10px;
  border-radius: 12px;
  border: 1px solid var(--color-border-default, #ddd);
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}
.slb-btn:hover:not(:disabled) { border-color: var(--color-accent); color: var(--color-text-primary); }
.slb-btn.on {
  border-color: var(--color-accent);
  background: var(--color-accent-light, rgba(99, 102, 241, 0.12));
  color: var(--color-text-primary);
}
.slb-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.slb-read {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--color-text-secondary);
  cursor: pointer;
}
.slb-read input { margin: 0; cursor: pointer; }
.slb-passed { color: var(--color-green, #22a06b); font-weight: 600; }
.slb-ai-mark { color: var(--color-text-tertiary); }
.slb-progress {
  margin-left: auto;
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
}

.section-learn-tag {
  flex-shrink: 0;
  margin-left: 4px;
  padding: 0 5px;
  border-radius: 9px;
  font-size: 11px;
  line-height: 15px;
}
.lp-passed { background: var(--color-green-light, rgba(34, 160, 107, 0.15)); color: var(--color-green, #22a06b); }
.lp-understood { background: var(--color-accent-light, rgba(99, 102, 241, 0.12)); color: var(--color-text-secondary); }
.lp-confused { background: var(--color-red-light, rgba(224, 86, 86, 0.15)); color: var(--color-red, #e05656); }
.lp-read { background: var(--color-bg-hover, rgba(127, 127, 127, 0.12)); color: var(--color-text-tertiary); }
/* AI 讲完自动记的「已懂 / 不懂」：同色系加一圈细描边，与“学生自己标的”区分开
   （用 inset box-shadow 而不是 border，避免影响布局尺寸） */
.lp-ai { box-shadow: inset 0 0 0 1px currentColor; }

/* 顶部栏「去学习」：用主色，与工具类按钮（编辑/关闭）区分开 */
.learn-btn {
  border-color: var(--color-accent);
  background: var(--color-accent-light, rgba(99, 102, 241, 0.1));
  color: var(--color-text-primary);
}
</style>
