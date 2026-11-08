<script setup>
import { watch, nextTick, ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useChatStore } from '../stores/chatStore'
import MessageBubble from './MessageBubble.vue'
import InputArea from './InputArea.vue'

const emit = defineEmits(['navigate-to-node'])

const store = useChatStore()
const listRef = ref(null)
const railRef = ref(null)

// 贴底判定容差（px）：在此范围内视为已在底部，隐藏「回到底部」按钮
const BOTTOM_THRESHOLD = 36

const atBottom = ref(true)
// LLM 生成期间的「持续跟随到底部」开关：生成结束或用户主动上滚即停止
const autoFollow = ref(false)
// 非生成态下点击按钮触发平滑滚动时的过渡锁，让按钮点击即消失、中途不闪烁
const jumpingToBottom = ref(false)

// 右侧对话轮次进度条（中心对称）：悬停轮次索引、预览卡片纵向坐标、当前阅读轮次
const hoverTurnIdx = ref(-1)
const previewTop = ref(0)
const activeTurnIdx = ref(0)

let lastScrollTop = 0
let isProgrammaticScroll = false
let touchStartY = 0
let jumpTimer = null
let mutationObserver = null
// scrollToBottomInstant 每帧都可能被调，rAF 句柄要留一个以便卸载时取消（残留回调会在
// 组件已经拆掉之后再去写 isProgrammaticScroll）
let rafId = 0

/** 去除 Markdown 标记，供右侧进度条悬浮卡片展示干净的纯文本摘要 */
function stripMarkdown(md = '') {
  return String(md || '')
    .slice(0, 400)   // 只用于摘要：先截断再跑 8 条正则，否则流式每 token 都全文重算
    .replace(/```[\s\S]*?```/g, ' [代码] ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^[>\s#*+-]+/gm, '')
    .replace(/[*_~|]+/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

/** 按「用户提问 + AI 回复」聚合成轮次列表（一轮对应右侧进度条上的一根刻度） */
const turns = computed(() => {
  const msgs = store.currentMessages || []
  const list = []
  for (let i = 0; i < msgs.length; i++) {
    const m = msgs[i]
    if (m.role === 'user') {
      list.push({
        index: list.length,
        userMsgIdx: i,
        userText: (m.content || '').replace(/\s+/g, ' ').trim(),
        aiText: '',
      })
    } else if (m.role === 'assistant' && list.length > 0) {
      const cur = list[list.length - 1]
      const clean = stripMarkdown(m.content)
      if (clean) {
        cur.aiText = cur.aiText ? `${cur.aiText} ${clean}` : clean
      }
    }
  }
  return list
})

const hoveredTurn = computed(() =>
  hoverTurnIdx.value >= 0 ? turns.value[hoverTurnIdx.value] || null : null
)

/** 轮次 ≤ 40 时固定间距随轮次变长；超长对话自动收紧间距避免超出视口 */
const tickPadY = computed(() => {
  const n = turns.value.length
  return n > 40 ? Math.max(1.5, Math.min(4, 160 / n)) : 4
})

function tickLevelClass(idx) {
  if (hoverTurnIdx.value >= 0) {
    const d = Math.abs(idx - hoverTurnIdx.value)
    if (d === 0) return 'is-hover-0'
    if (d === 1) return 'is-hover-1'
    if (d === 2) return 'is-hover-2'
    return ''
  }
  return idx === activeTurnIdx.value ? 'is-active' : ''
}

/** 刻度的可访问名：读数 + 该轮用户提问的前 20 字（纯 <div> 点击区读屏器读不出意图） */
function tickLabel(turn) {
  const text = (turn?.userText || '').trim()
  if (!text) return '空提问'
  return text.length > 20 ? text.slice(0, 20) + '…' : text
}

function handleTickEnter(idx, e) {
  hoverTurnIdx.value = idx
  const itemEl = e.currentTarget
  if (itemEl) {
    previewTop.value = itemEl.offsetTop + itemEl.offsetHeight / 2
  }
}

function scrollToTurn(turn) {
  const el = listRef.value
  if (!el || !turn) return
  const target = el.querySelector(`[data-msg-idx="${turn.userMsgIdx}"]`)
  if (!target) return
  stopAutoFollow()
  activeTurnIdx.value = turn.index
  el.scrollTo({ top: Math.max(0, target.offsetTop - 20), behavior: 'smooth' })
}

function updateActiveTurn() {
  const el = listRef.value
  const list = turns.value
  if (!el || !list.length) {
    activeTurnIdx.value = 0
    return
  }
  if (isNearBottom()) {
    activeTurnIdx.value = list.length - 1
    return
  }
  const probeY = el.scrollTop + el.clientHeight * 0.35
  // 一次取全部气泡再按 idx 定位：原来对每一轮各跑一次 `[data-msg-idx=…]` 属性选择器，
  // 40 轮 scroll 事件就是 40 次查询 + 40 次 offsetTop（幂等的重复劳动）。
  const byIdx = new Map()
  for (const node of el.querySelectorAll('[data-msg-idx]')) {
    byIdx.set(Number(node.dataset.msgIdx), node)
  }
  let current = 0
  for (let i = 0; i < list.length; i++) {
    const node = byIdx.get(list[i].userMsgIdx)
    if (node && node.offsetTop <= probeY) {
      current = i
    } else if (node && node.offsetTop > probeY) {
      break
    }
  }
  activeTurnIdx.value = current
}

const showScrollBtn = computed(() =>
  store.currentMessages.length > 0
  && !atBottom.value
  && !autoFollow.value
  && !jumpingToBottom.value
)

function isNearBottom() {
  const el = listRef.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_THRESHOLD
}

/** 立即滚到底部（流式持续跟随 / 切换对话用，避免 smooth 动画被高频 token 反复打断） */
function scrollToBottomInstant() {
  const el = listRef.value
  if (!el) return
  isProgrammaticScroll = true
  el.scrollTop = el.scrollHeight
  lastScrollTop = el.scrollTop
  atBottom.value = true
  activeTurnIdx.value = Math.max(0, turns.value.length - 1)
  if (rafId) cancelAnimationFrame(rafId)
  rafId = requestAnimationFrame(() => {
    rafId = 0
    isProgrammaticScroll = false
  })
}

/** 重置平滑滚动的静止检测定时器：只要还在往下滚就保持按钮隐藏，滚停后再兜底释放 */
function armJumpIdleTimer() {
  if (jumpTimer) clearTimeout(jumpTimer)
  jumpTimer = setTimeout(() => {
    jumpingToBottom.value = false
    isProgrammaticScroll = false
    if (listRef.value) {
      lastScrollTop = listRef.value.scrollTop
      atBottom.value = isNearBottom()
    }
  }, 160)
}

/** 单次滚到底部后停止（非生成态点击向下箭头按钮） */
function scrollToBottomOnce() {
  const el = listRef.value
  if (!el) return
  autoFollow.value = false
  jumpingToBottom.value = true
  isProgrammaticScroll = true
  el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  armJumpIdleTimer()
}

/** 停止持续跟随（用户主动向上滚动时触发） */
function stopAutoFollow() {
  if (autoFollow.value) autoFollow.value = false
  if (jumpingToBottom.value) {
    jumpingToBottom.value = false
    isProgrammaticScroll = false
    if (jumpTimer) {
      clearTimeout(jumpTimer)
      jumpTimer = null
    }
  }
}

function handleWheel(e) {
  if (e.deltaY < 0) {
    stopAutoFollow()
    atBottom.value = isNearBottom()
  }
}

function handleTouchStart(e) {
  touchStartY = e.touches?.[0]?.clientY ?? 0
}

function handleTouchMove(e) {
  const curY = e.touches?.[0]?.clientY ?? 0
  // 手指向下划 = 页面往上滚
  if (curY - touchStartY > 6) {
    stopAutoFollow()
    atBottom.value = isNearBottom()
  }
}

function handleScroll() {
  const el = listRef.value
  if (!el) return
  const curTop = el.scrollTop
  // 非程序触发的向上位移（如拖动滚动条、方向键/PageUp）→ 立即停止持续滚动
  if (!isProgrammaticScroll && curTop < lastScrollTop - 2) {
    stopAutoFollow()
  }
  lastScrollTop = curTop
  const near = isNearBottom()
  atBottom.value = near
  updateActiveTurn()
  if (jumpingToBottom.value) {
    if (near) {
      jumpingToBottom.value = false
      isProgrammaticScroll = false
      if (jumpTimer) {
        clearTimeout(jumpTimer)
        jumpTimer = null
      }
    } else {
      // 平滑滚动仍在进行中，续期静止定时器，防止长距离滚动中途按钮冒出来
      armJumpIdleTimer()
    }
  }
}

/**
 * 点击输入框上方的圆形按钮：
 *   - LLM 正在生成（显示 3 个点）：开启持续滚动到底部（按钮立即消失），直至生成结束或用户上滚
 *   - LLM 不在生成（显示向下箭头）：单次滚到底部即停
 */
function handleScrollBtnClick() {
  if (store.loading) {
    autoFollow.value = true
    nextTick(() => scrollToBottomInstant())
  } else {
    scrollToBottomOnce()
  }
}

// LLM 生成状态切换：开始生成时开启持续跟随；生成结束立即停止持续跟随
watch(
  () => store.loading,
  (loading) => {
    if (loading) {
      autoFollow.value = true
      nextTick(() => scrollToBottomInstant())
    } else {
      const wasFollowing = autoFollow.value
      autoFollow.value = false
      if (wasFollowing) {
        nextTick(() => scrollToBottomInstant())
      } else {
        nextTick(() => { atBottom.value = isNearBottom() })
      }
    }
  }
)

// 消息数量变化（如发送新消息、收到题卡）
watch(
  () => store.currentMessages.length,
  () => {
    if (autoFollow.value || atBottom.value) {
      nextTick(() => scrollToBottomInstant())
    } else {
      nextTick(() => { atBottom.value = isNearBottom() })
    }
  }
)

// 切换对话时无条件滚到底部并重置状态
watch(
  () => store.currentId,
  () => {
    autoFollow.value = store.loading
    jumpingToBottom.value = false
    nextTick(() => scrollToBottomInstant())
  }
)

// 流式内容变化时：若处于持续跟随态则锁底，否则仅更新是否贴底（让上滚用户的按钮能及时浮现）
watch(
  () => {
    const msgs = store.currentMessages
    if (msgs.length === 0) return ''
    const last = msgs[msgs.length - 1]
    return `${last?.content?.length ?? 0}:${last?.thinking?.length ?? 0}:${last?.tools?.length ?? 0}`
  },
  () => {
    if (autoFollow.value) {
      nextTick(() => scrollToBottomInstant())
    } else {
      nextTick(() => { atBottom.value = isNearBottom() })
    }
  }
)

onMounted(() => {
  if (!listRef.value) return
  mutationObserver = new MutationObserver(() => {
    if (autoFollow.value && store.loading) {
      scrollToBottomInstant()
    } else if (!jumpingToBottom.value) {
      atBottom.value = isNearBottom()
    }
  })
  // 只观察结构变化（新气泡/工具卡）。**不要 characterData**：流式每来一个 token 都会
  // 触发一次回调，与上面 content-length 的 watch 形成两套每-token 滚动，且两次都读
  // scrollHeight（强制同步布局）。
  mutationObserver.observe(listRef.value, {
    childList: true,
    subtree: true,
  })
})

onBeforeUnmount(() => {
  mutationObserver?.disconnect()
  mutationObserver = null
  if (jumpTimer) clearTimeout(jumpTimer)
  if (rafId) {
    cancelAnimationFrame(rafId)
    rafId = 0
  }
})

const greetingMessages = [
  '你好！我是你的 AI 学习助手，有什么想学的内容吗？',
  '可以把问题告诉我，我来陪你一起把思路理顺～',
]
</script>

<template>
  <div class="chat-area">
    <!-- ChatArea 内部不再有顶部栏 — 顶部栏已移到 App.vue 统一管理 -->

    <div class="chat-body">
      <!-- 消息列表 -->
      <main
        class="message-list"
        ref="listRef"
        @scroll.passive="handleScroll"
        @wheel.passive="handleWheel"
        @touchstart.passive="handleTouchStart"
        @touchmove.passive="handleTouchMove"
      >
        <template v-if="store.currentMessages.length > 0">
          <MessageBubble
            v-for="(msg, idx) in store.currentMessages"
            :key="idx"
            :data-msg-idx="idx"
            :message="msg"
            :fallback-ts="store.currentConversation?.updatedAt || store.currentConversation?.createdAt || 0"
            :knowledge-nodes="store.knowledgeNodes"
            @navigate-to-node="(nodeId) => emit('navigate-to-node', nodeId)"
            @retry="store.retryLast()"
            @submit-quiz="(p) => store.send(p.answer)"
          />
        </template>

        <!-- 空白欢迎 -->
        <div v-else class="welcome">
          <!-- 描边 SVG 图标（lucide sparkles），替代原来的 🧠 emoji -->
          <div class="welcome-icon">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z" />
              <path d="M5 3v4" />
              <path d="M19 17v4" />
              <path d="M3 5h4" />
              <path d="M17 19h4" />
            </svg>
          </div>
          <h3>有什么想学的？</h3>
          <p v-for="(g, i) in greetingMessages" :key="i">{{ g }}</p>
        </div>
      </main>

      <!-- 最右侧：对话轮次刻度进度条（中心对称，随对话增多变长，悬停波浪凸起 + 预览问答摘要） -->
      <aside
        v-if="turns.length > 0"
        ref="railRef"
        class="turn-rail"
        aria-label="对话轮次导航"
        @mouseleave="hoverTurnIdx = -1"
      >
        <button
          v-for="t in turns"
          :key="t.index"
          type="button"
          class="turn-tick-item"
          :style="{ paddingTop: tickPadY + 'px', paddingBottom: tickPadY + 'px' }"
          :aria-label="`跳到第 ${t.index + 1} 轮：${tickLabel(t)}`"
          @mouseenter="handleTickEnter(t.index, $event)"
          @focus="handleTickEnter(t.index, $event)"
          @click="scrollToTurn(t)"
        >
          <span class="turn-tick-bar" :class="tickLevelClass(t.index)" />
        </button>

        <!-- 悬停预览浮窗：展示该轮用户输入与 LLM 回复截取 -->
        <Transition name="turn-preview-fade">
          <button
            v-if="hoveredTurn"
            type="button"
            class="turn-preview"
            :style="{ top: previewTop + 'px' }"
            :aria-label="`跳到第 ${hoveredTurn.index + 1} 轮：${tickLabel(hoveredTurn)}`"
            @click="scrollToTurn(hoveredTurn)"
          >
            <div class="turn-preview-user">{{ hoveredTurn.userText || '（空输入）' }}</div>
            <div class="turn-preview-ai">
              {{
                hoveredTurn.aiText
                  || (store.loading && hoveredTurn.index === turns.length - 1 ? 'AI 思考中…' : '暂无回复')
              }}
            </div>
          </button>
        </Transition>
      </aside>

      <!-- 输入框上方的「返回最下面」圆形悬浮按钮 -->
      <Transition name="scroll-btn-fade">
        <button
          v-if="showScrollBtn"
          type="button"
          class="scroll-bottom-btn"
          :title="store.loading ? '持续滚动到底部' : '回到最下面'"
          :aria-label="store.loading ? '持续滚动到底部' : '回到最下面'"
          @click="handleScrollBtnClick"
        >
          <!-- LLM 正在生成：显示 3 个点 -->
          <span v-if="store.loading" class="scroll-dots" aria-hidden="true">
            <span></span><span></span><span></span>
          </span>
          <!-- LLM 不在生成：显示向下箭头 -->
          <svg
            v-else
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <line x1="12" y1="5" x2="12" y2="19" />
            <polyline points="19 12 12 19 5 12" />
          </svg>
        </button>
      </Transition>
    </div>

    <!-- 底部输入 -->
    <InputArea />
  </div>
</template>

<style scoped>
.chat-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--color-bg-primary);
  min-width: 0;
}

.chat-body {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  /* 供右侧刻度条做容器查询：小窗（FloatingBall 340×480）里没有横向余量 */
  container-type: inline-size;
}

/* 消息列表：左右最大留白 = max(24px, 剩余空间/2)，把消息列封顶在 760px 并居中
   （长文一行 100+ 字难以阅读；不改 DOM 结构，仅靠 padding 收窄内容区） */
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 24px max(24px, calc((100% - 760px) / 2)) 8px;
}

.message-list::-webkit-scrollbar {
  width: 5px;
}

.message-list::-webkit-scrollbar-thumb {
  background: var(--color-border-light);
  border-radius: 3px;
}

/* ── 右侧对话轮次刻度进度条（中心对称 + 悬停波浪凸起 + 轮次预览浮窗） ── */
.turn-rail {
  position: absolute;
  right: 10px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 12;
  max-height: calc(100% - 40px);
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  user-select: none;
}

/* 窄容器（悬浮小窗 compact=340 → .ball-win 有 overflow:hidden，且右侧内边距只有 24px）
   下刻度条会压住正文、预览卡（280px + 8px）必然被裁掉 —— 直接不渲染这一层。
   560px 的界：主视图最小宽度也在这个量级之上，桌面端不受影响。 */
@container (max-width: 560px) {
  .turn-rail {
    display: none;
  }
}

.turn-tick-item {
  width: 28px;
  padding-left: 4px;
  padding-right: 4px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  cursor: pointer;
  /* 用真 <button> 拿键盘可达与读屏语义 → 把浏览器按钮默认外观清干净 */
  appearance: none;
  border: 0;
  background: none;
  font: inherit;
  border-radius: 4px;
}

.turn-tick-item:focus-visible {
  outline: 2px solid var(--color-accent, var(--color-text-secondary));
  outline-offset: 1px;
}

.turn-tick-bar {
  display: block;
  width: 6px;
  height: 2px;
  border-radius: 999px;
  background: var(--color-text-tertiary);
  opacity: 0.42;
  transition:
    width 0.16s cubic-bezier(0.22, 1, 0.36, 1),
    height 0.16s ease,
    opacity 0.16s ease,
    background-color 0.16s ease;
}

.turn-tick-bar.is-active {
  width: 11px;
  background: var(--color-text-secondary);
  opacity: 0.72;
}

.turn-tick-bar.is-hover-2 {
  width: 10px;
  background: var(--color-text-tertiary);
  opacity: 0.58;
}

.turn-tick-bar.is-hover-1 {
  width: 15px;
  background: var(--color-text-secondary);
  opacity: 0.78;
}

.turn-tick-bar.is-hover-0 {
  width: 22px;
  height: 2.5px;
  background: var(--color-text-primary);
  opacity: 1;
}

/* 刻度左侧悬浮预览卡片（实心不透明底色，展示用户输入 + LLM 回复截取） */
.turn-preview {
  position: absolute;
  right: calc(100% + 8px);
  transform: translateY(-50%);
  width: 280px;
  max-width: min(280px, 55vw);
  padding: 10px 14px;
  border-radius: 12px;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-strong, var(--color-border));
  box-shadow: var(--shadow-popup);
  cursor: pointer;
  transition: top 0.12s cubic-bezier(0.22, 1, 0.36, 1);
  /* 卡片本身是 <button>（键盘可达 + 读屏可读），清掉按钮默认外观 */
  appearance: none;
  font: inherit;
  text-align: left;
}

.turn-preview-user {
  font-size: 13px;
  font-weight: 600;
  line-height: 1.4;
  color: var(--color-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 4px;
}

.turn-preview-ai {
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--color-text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

.turn-preview-fade-enter-active,
.turn-preview-fade-leave-active {
  transition: opacity 0.14s ease, transform 0.14s ease;
}

.turn-preview-fade-enter-from,
.turn-preview-fade-leave-to {
  opacity: 0;
  transform: translate(4px, -50%);
}

/* 输入框正上方的「回到底部」圆形悬浮按钮（保持实心不透明底色，避免透出下方对话文字） */
.scroll-bottom-btn {
  position: absolute;
  left: 50%;
  bottom: 12px;
  transform: translateX(-50%);
  z-index: 10;
  width: 34px;
  height: 34px;
  padding: 0;
  border-radius: 50%;
  border: 1px solid var(--color-border-strong, var(--color-border));
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  box-shadow: var(--shadow-md);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s, box-shadow 0.15s;
}

.scroll-bottom-btn:hover {
  background: var(--color-bg-card);
  color: var(--color-text-primary);
  border-color: var(--color-text-secondary);
  box-shadow: var(--shadow-lg);
}

/* LLM 生成中的 3 个点指示 */
.scroll-dots {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

.scroll-dots span {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: currentColor;
  animation: scrollDotPulse 1.2s ease-in-out infinite;
}

.scroll-dots span:nth-child(2) {
  animation-delay: 0.2s;
}

.scroll-dots span:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes scrollDotPulse {
  0%, 100% { opacity: 0.35; transform: scale(0.85); }
  50% { opacity: 1; transform: scale(1.1); }
}

.scroll-btn-fade-enter-active,
.scroll-btn-fade-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.scroll-btn-fade-enter-from,
.scroll-btn-fade-leave-to {
  opacity: 0;
  transform: translate(-50%, 6px);
}

/* 欢迎区 */
.welcome {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  text-align: center;
  color: var(--color-text-muted);
}

/* 欢迎图标：描边 SVG，颜色随主题主色（原为 56px emoji 字号） */
.welcome-icon {
  display: inline-flex;
  margin-bottom: 16px;
  color: var(--color-primary);
  opacity: 0.9;
}

.welcome-icon svg {
  width: 48px;
  height: 48px;
}

.welcome h3 {
  font-size: 22px;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 8px;
}

.welcome p {
  margin: 4px 0;
  font-size: 14px;
  line-height: 1.6;
  max-width: 380px;
}
</style>
