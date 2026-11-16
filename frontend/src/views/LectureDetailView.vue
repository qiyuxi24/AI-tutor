<script setup>
/**
 * LectureDetailView.vue — 视频课时页（一课 = 一串知识点）
 *
 * 要解决的旧体验：导入只产出"口语转写稿"（`[00:03:47] 剪对了是这样一个图` 这种），
 * 点开是一大坨文字 —— 没有时间轴、没有播放器、不能回跳原视频。
 * 本页把一课拆成「知识点」，每点带时间锚点 + 讲解 + 幻灯片截图，顶部一键跳回原视频那一秒。
 *
 * 三段式布局（对标 course2md 阅读器）：
 *   顶部   返回 / 标题 / 来源（平台·UP主·时长） / 「▶ 12:34 看原视频」
 *   左栏   知识点时间轴（LectureTimeline；窄屏折叠成顶部横向 chips）
 *   中栏   当前知识点的完整内容（大标题 / kind 标签 / brief 导语 / 大图 / 时间戳 / 正文）
 *
 * 四条契约上的纪律：
 *   ① `t_label` / `t_url` **一律用后端给的**，前端只做兜底。B站是 `?p=N&t=S`、
 *      YouTube 是 `?t=S`，前端各拼一次必然有一边错；
 *   ② 截图走 objectURL（`useSlideAssets`）—— 接口要鉴权，`<img src>` 带不了 JWT；
 *   ③ 正文（`segment.content`）是**不可信输入**，必须过 `renderMarkdown`（内含 DOMPurify）。
 *      当前契约只返回元信息（title/brief），所以正文是"有 content 才渲染"的前向兼容分支；
 *   ④ 结构化是**长任务**，结果不从 POST 响应回来 → 提交后轮询详情，直到
 *      `structured_at` 变了（只看 `structured` 布尔会在"重跑已结构化的集"时误判秒完）。
 *
 * Props:
 *   docId Number — 课时对应的 KB 文件节点 id（必填）
 *   title String — 加载期间先显示标题（避免页头先空一下）
 *   at    Number — 初始落点（秒）。从节点详情「视频出处」跳进来时用它定位分段，
 *                  因为那套接口只给 `locator`（`t=754`），没有 `seq`
 *
 * Emits:
 *   back () — 返回上一级（视频导入页）
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { getLectureDetail, restructureLecture } from '../api/kb.js'
import { kindLabel, pickSegment, sortSegments, formatClock, platformLabel } from '../utils/lecture.js'
import { useSlideAssets } from '../utils/slideAssets.js'
import { renderMarkdown } from '../utils/markdown.js'
import LectureTimeline from '../components/LectureTimeline.vue'
import AppIcon from '../components/AppIcon.vue'

const props = defineProps({
  docId: { type: Number, required: true },
  title: { type: String, default: '' },
  at: { type: Number, default: null },
})

const emit = defineEmits(['back'])

const detail = ref(null)
const loading = ref(true)
const error = ref('')
const activeSeq = ref(null)
const restructureBusy = ref(false)
const zoom = ref('')

/** 结构化轮询：3s 一次、最多 60 次（= 3 分钟）。与建图同理，长任务结果不走 HTTP 响应。 */
const POLL_INTERVAL = 3000
const POLL_LIMIT = 60
let pollTimer = null
let pollCount = 0

const segments = computed(() => sortSegments(detail.value?.segments || []))
const structured = computed(() => detail.value?.structured === true)
const watchUrl = computed(() => detail.value?.watch_url || '')
const durationLabel = computed(() => formatClock(detail.value?.duration))

const activeSegment = computed(() => {
  const list = segments.value
  if (!list.length) return null
  return list.find((s) => s.seq === activeSeq.value) || list[0]
})

/** 顶部按钮文案带上时间戳：用户点之前就知道会跳到哪一秒 */
const watchLabel = computed(() => {
  const seg = activeSegment.value
  return seg?.t_label ? `▶ ${seg.t_label} 看原视频` : '▶ 看原视频'
})

const sourceLine = computed(() => {
  const d = detail.value
  if (!d) return ''
  // platform 走后端枚举映射（`other` = Coursera/自录屏，直接显示英文对用户没意义）
  return [platformLabel(d.platform), d.uploader, durationLabel.value]
    .filter(Boolean).join(' · ')
})

/** 正文：接口现在不给 `content`，将来给了就直接渲染；两种都不能崩 */
const contentHtml = computed(() => {
  const md = activeSegment.value?.content
  return md ? renderMarkdown(md) : ''
})

const { urls: slideUrls, failed: slideFailed, load: loadSlides } = useSlideAssets()

/**
 * 整集是不是"只有兜底切片"：`kind === 'fallback'` 来自后端
 * `kb/lecture_structurer.py` 的时间等分兜底（模型不可用 / 本集无语音时）。
 * 判"全部"而不是"任意"：混有真知识点时，提示应该消失 —— 否则用户会以为
 * 真的知识点也是假的。
 */
const isFallbackOnly = computed(() => {
  const list = segments.value
  return list.length > 0 && list.every((s) => s?.kind === 'fallback')
})

/**
 * 中栏大图的截图单独取一次：左栏那批是缩略图，窄屏下左栏整个折叠，
 * 不能指望它已经替中栏取过图（已取过的地址会命中缓存，不重复请求）。
 */
watch(activeSegment, (seg) => {
  if (seg?.slide_url) loadSlides([seg.slide_url])
}, { immediate: true })

function windowOpen(url) {
  if (!url) return
  window.open(url, '_blank', 'noopener')
}

function selectSegment(seg) {
  activeSeq.value = seg.seq
}

function stopPoll() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
  pollCount = 0
}

/**
 * 结构化进度轮询。
 *
 * 完成判据是 **`structured_at` 变了**，不是 `structured` 变 true：
 * 已经结构化过的再重跑，`structured` 全程恒为 true，只看它的话第一轮就"秒完"，
 * 而后台其实还在跑。后端因此单独给出 `structured_at`，提交时记下旧值，变了才算完。
 * （首次结构化时旧值是空串，也能正确判出"从无到有"。）
 */
function startPoll() {
  stopPoll()
  const before = detail.value?.structured_at || ''
  pollTimer = setInterval(async () => {
    pollCount += 1
    try {
      const { data } = await getLectureDetail(props.docId)
      detail.value = data
      if (data?.structured_at && data.structured_at !== before) {
        stopPoll()
        restructureBusy.value = false
        ElMessage.success('这集已经结构化完成')
        return
      }
    } catch {
      // 单轮失败不终止：后台任务与这次请求无关，下一轮再试
    }
    if (pollCount >= POLL_LIMIT) {
      stopPoll()
      restructureBusy.value = false
      ElMessage.info('还在处理中，完成后刷新本页即可看到知识点')
    }
  }, POLL_INTERVAL)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await getLectureDetail(props.docId)
    detail.value = data
    // 指定了 at（来自「视频出处」的 locator）就落到覆盖那一秒的分段，否则第一段
    const hit = props.at != null ? pickSegment(data?.segments, props.at) : null
    activeSeq.value = hit?.seq ?? sortSegments(data?.segments || [])[0]?.seq ?? null
  } catch (e) {
    const status = e.response?.status
    error.value = status === 404
      ? '这集不存在或已被删除'
      : (e.response?.data?.detail || e.message || '加载失败')
    detail.value = null
  } finally {
    loading.value = false
  }
}

/** 「结构化这集」：提交后立刻确认"已开始"，然后轮询到 structured 变 true */
async function restructure() {
  if (restructureBusy.value) return
  restructureBusy.value = true
  try {
    await restructureLecture(props.docId)
    ElMessage.success('已开始处理，完成后会通知你；也可以留在这页等结果')
    startPoll()
  } catch (e) {
    restructureBusy.value = false
    ElMessage.error('提交失败：' + (e.response?.data?.detail || e.message))
  }
}

/** 换课时：定时器必须清掉（否则离开这一集后还在打上一集的接口） */
watch(() => props.docId, () => {
  stopPoll()
  restructureBusy.value = false
  activeSeq.value = null
  load()
}, { immediate: true })

/**
 * 落在**同一节课的不同秒**（连着点两个知识点的「视频出处」）：docId 没变，
 * 上面的 watch 不会触发，所以单独跟一次 `at` —— 只挪选中项，不重拉详情。
 */
watch(() => props.at, (val) => {
  if (val == null) return
  if (!segments.value.length) { load(); return }
  const hit = pickSegment(segments.value, val)
  if (hit) activeSeq.value = hit.seq
})

onBeforeUnmount(stopPoll)
</script>

<template>
  <div class="ld">
    <!-- ── 顶部：来源与「看原视频」是这页的主操作 ── -->
    <header class="ld-head">
      <button type="button" class="ld-back" title="返回视频导入" @click="emit('back')">
        <AppIcon name="chevronLeft" :size="16" />
        <span>返回</span>
      </button>

      <div class="ld-head-main">
        <h1 class="ld-title" :title="detail?.title || title">
          {{ detail?.title || title || '课时' }}
        </h1>
        <p v-if="sourceLine" class="ld-source">{{ sourceLine }}</p>
      </div>

      <button
        type="button"
        class="ld-watch"
        :disabled="!watchUrl"
        :title="watchUrl ? '在新标签页打开原视频的这一秒' : '这集没有可跳转的原视频链接'"
        @click="windowOpen(watchUrl)"
      >
        <template v-if="watchUrl">{{ watchLabel }}</template>
        <template v-else>无原视频链接</template>
      </button>
    </header>

    <!-- ── 主体：左时间轴 + 中内容 ── -->
    <div class="ld-body">
      <!-- 没有分段时不占一条边（状态说明放在中栏，避免"左边空着说没有"） -->
      <aside v-if="loading || segments.length" class="ld-aside">
        <LectureTimeline
          :segments="segments"
          :active-seq="activeSeq"
          :loading="loading"
          @select="selectSegment"
        />
      </aside>

      <main class="ld-main">
        <!-- 加载 -->
        <div v-if="loading" class="ld-state">
          <span class="ld-spinner" aria-hidden="true" />
          <p class="ld-state-desc">正在读取这集的知识点…</p>
        </div>

        <!-- 出错 / 404：给重试，不给死路 -->
        <div v-else-if="error" class="ld-state">
          <p class="ld-state-title">读取出错</p>
          <p class="ld-state-desc">{{ error }}</p>
          <button type="button" class="ld-btn primary" @click="load">重试</button>
        </div>

        <!-- 未结构化：说清现状 + 给一条出路（不能白屏） -->
        <div v-else-if="!structured" class="ld-state">
          <p class="ld-state-title">这集还是转写稿，未结构化</p>
          <p class="ld-state-desc">
            现在的正文是语音转写的流水稿（带 ASR 错字、没有时间轴）。
            结构化后会切成一个个知识点：标题、时间锚点、讲解、幻灯片截图。
          </p>
          <div class="ld-state-actions">
            <button
              type="button"
              class="ld-btn primary"
              :disabled="restructureBusy"
              @click="restructure"
            >
              {{ restructureBusy ? '处理中…' : '结构化这集' }}
            </button>
            <button v-if="watchUrl" type="button" class="ld-btn" @click="windowOpen(watchUrl)">
              先看原视频
            </button>
          </div>
          <p v-if="restructureBusy" class="ld-state-hint">
            已提交，后台在处理。完成后会自动刷新，也可以直接离开这一页。
          </p>
        </div>

        <!-- 已结构化但一段都没切出来 -->
        <div v-else-if="!segments.length" class="ld-state">
          <p class="ld-state-title">这集没切出知识点</p>
          <p class="ld-state-desc">
            结构化跑完了，但没有识别出可用的知识点分段（可能是纯板书、无讲解，或字幕太短）。
            可以重跑一次，或直接看原视频。
          </p>
          <div class="ld-state-actions">
            <button
              type="button"
              class="ld-btn primary"
              :disabled="restructureBusy"
              @click="restructure"
            >
              {{ restructureBusy ? '处理中…' : '重新结构化' }}
            </button>
            <button v-if="watchUrl" type="button" class="ld-btn" @click="windowOpen(watchUrl)">
              看原视频
            </button>
          </div>
        </div>

        <!-- 正常：当前知识点的完整内容 -->
        <article v-else-if="activeSegment" class="ld-seg">
          <!-- 兜底切片提示：kind='fallback' 表示这集没拿到语义切片，当前是按时间等分
               （模型不可用 / 只有转写稿）。必须说出来 —— 否则用户会以为这集真被切成了
               知识点。重跑一次通常就能拿到语义切片。 -->
          <p v-if="isFallbackOnly" class="ld-degraded">
            这集的分段是<strong>按时间等分</strong>的兜底结果，还不是真正的知识点边界
            （模型不可用或本集无语音）。点右上「重新结构化」会用已有转写稿再切一次；
            按时间跳转、看原文照常可用。
          </p>

          <h2 class="ld-seg-title">{{ activeSegment.title || '未命名知识点' }}</h2>

          <div class="ld-seg-meta">
            <span class="ld-kind">{{ kindLabel(activeSegment.kind) }}</span>
            <!-- 时间戳可点：直接跳原视频这一秒（t_url 由后端拼好；空串退化成纯文字） -->
            <button
              v-if="activeSegment.t_url"
              type="button"
              class="ld-time"
              :title="`在原视频打开 ${activeSegment.t_label || ''}`"
              @click="windowOpen(activeSegment.t_url)"
            >
              {{ activeSegment.t_label }}
            </button>
            <span v-else class="ld-time is-plain">{{ activeSegment.t_label }}</span>
            <!-- node_ok === false = 关联的图谱节点已删除：只给文字，不给会 404 的链接 -->
            <span v-if="activeSegment.node_id" class="ld-node">
              <template v-if="activeSegment.node_ok === false">
                <span class="ld-node-dead">已关联节点已删除</span>
              </template>
              <template v-else>已关联知识点</template>
            </span>
          </div>

          <!-- brief 当导语：浅底 + 左主色条，与正文视觉分层 -->
          <blockquote v-if="activeSegment.brief" class="ld-brief">
            {{ activeSegment.brief }}
          </blockquote>

          <!-- 大图：点开全屏（板书/公式在 780px 栏里本来就偏小） -->
          <figure v-if="activeSegment.slide_url" class="ld-figure">
            <img
              v-if="slideUrls[activeSegment.slide_url]"
              :src="slideUrls[activeSegment.slide_url]"
              :alt="activeSegment.title || '幻灯片截图'"
              loading="lazy"
              @click="zoom = slideUrls[activeSegment.slide_url]"
            />
            <figcaption v-else-if="slideFailed[activeSegment.slide_url]" class="ld-figure-note">
              截图加载失败
            </figcaption>
            <figcaption v-else class="ld-figure-note">截图加载中…</figcaption>
          </figure>

          <!-- 正文：接口暂时不给 content；给了就走 renderMarkdown（已消毒） -->
          <div v-if="contentHtml" class="markdown-body ld-content" v-html="contentHtml" />
          <p v-else class="ld-content-empty">
            这集目前只有知识点的标题与摘要，还没有逐段讲解正文。
          </p>
        </article>
      </main>
    </div>

    <!-- 图片放大：点遮罩关闭（与讲义预览同一套交互） -->
    <div v-if="zoom" class="ld-zoom" @click.self="zoom = ''">
      <img :src="zoom" alt="放大查看" class="ld-zoom-img" />
      <button type="button" class="ld-zoom-close" @click="zoom = ''">关闭</button>
    </div>
  </div>
</template>

<style scoped>
/* 整页 flex 纵向：顶部固定、主体吃满剩余高度（内部各自滚动，页面本身不滚） */
.ld {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background: var(--color-bg-page);
}

/* ── 顶部 ── */
.ld-head {
  display: flex;
  align-items: flex-start;
  gap: var(--space-md);
  flex-shrink: 0;
  padding: var(--space-md) var(--space-xl);
  border-bottom: 1px solid var(--color-border-default);
  background: var(--color-bg-card);
}

.ld-back {
  display: inline-flex;
  align-items: center;
  gap: var(--space-xs);
  flex-shrink: 0;
  margin-top: 2px;
  padding: var(--space-xs) var(--space-sm);
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: var(--font-size-xs);
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.ld-back:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
  border-color: var(--color-border-strong);
}

.ld-head-main { flex: 1; min-width: 0; }

.ld-title {
  margin: 0;
  font-size: var(--font-size-lg);
  font-weight: 600;
  line-height: var(--line-height-sm);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ld-source {
  margin: var(--space-xs) 0 0;
  font-size: var(--font-size-xs);
  color: var(--color-text-placeholder);
}

/* 主操作：全页只有它用实心主色（"看原视频"是这页唯一的目的地） */
.ld-watch {
  flex-shrink: 0;
  padding: var(--space-sm) var(--space-md);
  border: none;
  border-radius: var(--radius-md);
  background: var(--color-primary);
  color: var(--color-text-inverse);
  font-family: inherit;
  font-size: var(--font-size-xs);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  cursor: pointer;
  transition: background 0.15s;
}
.ld-watch:hover:not(:disabled) { background: var(--color-primary-dark); }
.ld-watch:disabled {
  background: var(--color-bg-disabled);
  color: var(--color-text-placeholder);
  cursor: default;
}

/* ── 主体 ── */
.ld-body {
  flex: 1;
  min-height: 0;
  display: flex;
}

/* 左栏固定宽度（300-340 区间），自身滚动 —— 时间轴可能几十段 */
.ld-aside {
  width: 324px;
  flex-shrink: 0;
  min-height: 0;
  border-right: 1px solid var(--color-border-default);
  background: var(--color-bg-card);
}

.ld-main {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: var(--space-xl);
}

/* ── 中栏内容 ── */
/* 限宽 + 居中：780px 是中文长文比较舒服的一行宽度（= --page-w-narrow） */
.ld-seg {
  max-width: var(--page-w-narrow);
  margin: 0 auto;
}

.ld-seg-title {
  margin: 0 0 var(--space-md);
  font-size: var(--font-size-xl);
  font-weight: 600;
  line-height: var(--line-height-sm);
  color: var(--color-text-primary);
}

.ld-seg-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-sm);
  margin-bottom: var(--space-lg);
  font-size: var(--font-size-xs);
}

.ld-kind {
  padding: 1px var(--space-sm);
  border-radius: var(--radius-sm);
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-weight: 600;
}

.ld-time {
  padding: 0;
  border: none;
  background: transparent;
  color: var(--color-primary);
  font-family: var(--font-mono);
  font-size: var(--font-size-xs);
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}
.ld-time:hover { text-decoration: underline; }
.ld-time.is-plain { cursor: default; }

.ld-node { color: var(--color-text-placeholder); }
.ld-node-dead { color: var(--color-warning); }

/* 导语：左主色条 + 浅底，与正文分开 */
.ld-brief {
  margin: 0 0 var(--space-lg);
  padding: var(--space-md) var(--space-lg);
  border-left: 3px solid var(--color-primary);
  border-radius: var(--radius-sm);
  background: var(--color-primary-soft);
  color: var(--color-text-secondary);
  font-size: var(--font-size-base);
  line-height: var(--line-height-lg);
}

/* 兜底切片提示：警告色而不是主色 —— 这是"数据还不完整"的告知，不是强调内容。
   与 .ld-brief 用不同的左边框色，避免被当成知识点导语。 */
.ld-degraded {
  margin: 0 0 var(--space-lg);
  padding: var(--space-sm) var(--space-md);
  border-left: 3px solid var(--color-warning);
  border-radius: var(--radius-sm);
  background: var(--color-warning-soft);
  color: var(--color-text-secondary);
  font-size: var(--font-size-sm);
  line-height: var(--line-height-base);
}
.ld-degraded strong { color: var(--color-text-primary); }

.ld-figure { margin: 0 0 var(--space-lg); }
.ld-figure img {
  display: block;
  width: 100%;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  background: var(--color-bg-hover);
  cursor: zoom-in;
}
.ld-figure-note {
  padding: var(--space-2xl) 0;
  border: 1px dashed var(--color-border-default);
  border-radius: var(--radius-md);
  font-size: var(--font-size-xs);
  color: var(--color-text-placeholder);
  text-align: center;
}

.ld-content-empty {
  margin: 0;
  padding: var(--space-md) 0;
  font-size: var(--font-size-xs);
  color: var(--color-text-placeholder);
}

/* ── 正文排版（仅 `segment.content` 存在时才用得上）──
   `.markdown-body` 的排版规则在 DocWorkspace 里是 scoped（只作用于那个组件），
   本页 v-html 出来的是裸 HTML，不补这几条就会挤成一坨。
   只补阅读必需的几条（段落/标题/列表/代码/表格/引用），不复制一整套排版体系。 */
.ld-content {
  font-size: var(--font-size-base);
  line-height: var(--line-height-lg);
  color: var(--color-text-primary);
  word-break: break-word;
}
.ld-content :deep(p) { margin: 0 0 var(--space-md); }
.ld-content :deep(strong) { font-weight: 600; }
.ld-content :deep(a) { color: var(--color-primary); }
.ld-content :deep(h1),
.ld-content :deep(h2),
.ld-content :deep(h3),
.ld-content :deep(h4) {
  margin: var(--space-lg) 0 var(--space-sm);
  font-weight: 600;
  line-height: var(--line-height-sm);
  color: var(--color-text-primary);
}
.ld-content :deep(h1) { font-size: var(--font-size-lg); }
.ld-content :deep(h2) { font-size: var(--font-size-md); }
.ld-content :deep(h3) { font-size: var(--font-size-base); }
.ld-content :deep(ul),
.ld-content :deep(ol) { margin: var(--space-sm) 0; padding-left: var(--space-xl); }
.ld-content :deep(li) { margin: 2px 0; }
.ld-content :deep(code) {
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  background: var(--color-bg-hover);
  font-family: var(--font-mono);
  font-size: 0.92em;
}
.ld-content :deep(pre) {
  margin: var(--space-md) 0;
  padding: var(--space-md) var(--space-lg);
  border-radius: var(--radius-md);
  background: var(--color-bg-hover);
  overflow-x: auto;
}
.ld-content :deep(pre code) { padding: 0; background: transparent; }
.ld-content :deep(blockquote) {
  margin: var(--space-md) 0;
  padding-left: var(--space-lg);
  border-left: 3px solid var(--color-primary);
  color: var(--color-text-secondary);
}
.ld-content :deep(table) {
  border-collapse: collapse;
  margin: var(--space-md) 0;
  font-size: var(--font-size-sm);
}
.ld-content :deep(th),
.ld-content :deep(td) {
  padding: var(--space-xs) var(--space-sm);
  border: 1px solid var(--color-border-default);
}
.ld-content :deep(img) {
  max-width: 100%;
  border-radius: var(--radius-md);
}

/* ── 状态块（loading / error / 未结构化 / 空分段）── */
.ld-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-sm);
  max-width: 520px;
  margin: 12vh auto 0;
  text-align: center;
}
.ld-state-title {
  margin: 0;
  font-size: var(--font-size-md);
  font-weight: 600;
  color: var(--color-text-primary);
}
.ld-state-desc {
  margin: 0;
  font-size: var(--font-size-sm);
  line-height: var(--line-height-lg);
  color: var(--color-text-placeholder);
}
.ld-state-actions {
  display: flex;
  gap: var(--space-sm);
  margin-top: var(--space-sm);
}
.ld-state-hint {
  margin: var(--space-xs) 0 0;
  font-size: var(--font-size-xs);
  color: var(--color-primary);
}

.ld-btn {
  padding: var(--space-sm) var(--space-lg);
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: var(--font-size-sm);
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.ld-btn:hover:not(:disabled) {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
  border-color: var(--color-border-strong);
}
.ld-btn:disabled { opacity: 0.5; cursor: default; }
.ld-btn.primary {
  border-color: transparent;
  background: var(--color-primary);
  color: var(--color-text-inverse);
  font-weight: 600;
}
.ld-btn.primary:hover:not(:disabled) { background: var(--color-primary-dark); }

.ld-spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--color-bg-disabled);
  border-top-color: var(--color-primary);
  border-radius: var(--radius-full);
  animation: ld-spin 0.8s linear infinite;
}
@keyframes ld-spin { to { transform: rotate(360deg); } }

/* ── 图片放大 ── */
.ld-zoom {
  position: fixed;
  inset: 0;
  z-index: 1100;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-md);
  background: rgba(0, 0, 0, 0.8);
  cursor: zoom-out;
}
.ld-zoom-img {
  max-width: 92vw;
  max-height: 82vh;
  object-fit: contain;
  border-radius: var(--radius-md);
  background: #fff;
}
.ld-zoom-close {
  padding: 6px 14px;
  border: 1px solid rgba(255, 255, 255, 0.35);
  border-radius: var(--radius-full);
  background: transparent;
  color: #fff;
  font-family: inherit;
  font-size: var(--font-size-xs);
  cursor: pointer;
}

/* ── 窄屏：左栏折叠成顶部横向 chips（时间轴仍可点，只是不再占一条边）──
   断点取 900px：再窄，中栏正文（780px 限宽）就被挤到不可读了。 */
@media (max-width: 900px) {
  .ld-head { padding: var(--space-md); }
  .ld-body { flex-direction: column; }
  .ld-aside {
    width: auto;
    border-right: none;
    border-bottom: 1px solid var(--color-border-default);
    overflow-x: auto;
    overflow-y: hidden;
  }
  /* 横向一条：每项定宽不换行，靠横向滚动看后面的。
     `height: auto` 必须显式给 —— 组件内的 `.tl` 是 `height: 100%`（为竖排吃满左栏），
     横排时父容器高度由内容决定，100% 会解析成 0，整条时间轴会消失。 */
  .ld-aside :deep(.tl) { height: auto; padding: var(--space-sm); overflow: visible; }
  .ld-aside :deep(.tl-list) { flex-direction: row; gap: var(--space-sm); }
  .ld-aside :deep(.tl-item) {
    width: 180px;
    flex-shrink: 0;
    border-left: none;
    border-bottom: 3px solid transparent;
  }
  .ld-aside :deep(.tl-item.is-active) { border-bottom-color: var(--color-primary); }
  .ld-aside :deep(.tl-thumb) { max-height: 56px; }
  .ld-aside :deep(.tl-brief) { display: none; }
  .ld-main { padding: var(--space-lg); }
}
</style>
