<script setup>
/**
 * DocEditor.vue — Markdown 编辑器（Vditor 封装，**全站唯一**编辑入口）
 *
 * 为什么引 Vditor 而不是自己拿 textarea 拼（2026-11-15 的决定）：
 * 之前知识库与节点详情各自维护一套「textarea + 右侧 renderMarkdown 预览 + 手写格式按钮」，
 * 两份代码、两种手感，而且「所见即所得」根本做不出来（用户明确要求"原生直接编辑文字模式"）。
 * Vditor（MIT）一个库同时提供两种模式，正是需求本身：
 *   · `ir`（即时渲染）= 直接在正文里写字，Typora 那种；**默认**
 *   · `sv`（分屏源码）= 左码右预览，适合抠表格/公式这类排版
 * 两者可随时切换，内容不丢。
 *
 * 资源自托管（关键）：Vditor 的 Markdown 引擎 Lute 不是 npm 依赖的一部分，默认用
 * `<script src="https://unpkg.com/vditor@x/dist/js/lute/lute.min.js">` 从 CDN 拉
 * —— 本项目本地/内网部署，那样离线就白屏。所以 `scripts/copy-vditor-assets.mjs`
 * 在 predev/prebuild 把资源拷到 `public/vditor/`，这里把 `cdn` 指向 `/vditor`。
 *
 * 缓存必须关（`cache.enable: false`）：Vditor 默认按 id 把内容存 localStorage 并在下次
 * 打开时"恢复"，而我们是同一个组件复用来编辑**不同**文档 —— 开着它就会把上一份文档的
 * 内容恢复进来，是明确的数据事故。
 *
 * 与阅读态的关系：编辑用 Vditor 渲染，**阅读态仍走 `utils/markdown.js`**（全站统一，
 * 对话/图谱/知识库/节点同一套）。不把 Vditor 的渲染器带到阅读态，否则同一个知识点
 * 在读和编辑两种状态下长得不一样。
 *
 * Props:
 *   modelValue  String   正文（v-model）
 *   mode        'ir'|'sv'  当前编辑模式（默认 ir）
 *   height      String   编辑器高度（默认 '100%'，由父容器决定）
 * Emits:
 *   update:modelValue  内容变更
 *   ready               实例就绪（父级可据此聚焦）
 * Expose: focus()
 */
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useTheme } from '../utils/theme.js'

const props = defineProps({
  modelValue: { type: String, default: '' },
  mode: { type: String, default: 'ir' },   // 'ir' 即时渲染 / 'sv' 分屏源码
  height: { type: String, default: '100%' },
})

const emit = defineEmits(['update:modelValue', 'ready'])

const host = ref(null)
/** Vditor 实例：非响应式（内部持有大量 DOM 状态，深度代理会拖慢甚至出错） */
const vditor = shallowRef(null)
const ready = ref(false)

const { isDark } = useTheme()

let disposed = false

/** 工具栏：编辑用得上的那些，去掉上传/录音/表情等本项目没有后端的项。
 *
 *  刻意**不含** `edit-mode`：Vditor 自带那个菜单能让用户切到 `wysiwyg`，而本组件的
 *  模式权威在 `mode` prop（父级的「即时渲染 / 源码」分段按钮）。两个入口同时存在时，
 *  用户点 Vditor 菜单切到 wysiwyg，外层按钮却仍高亮「即时渲染」—— 状态两处维护必然
 *  显示错误。所以模式只留一个入口。 */
const TOOLBAR = [
  'headings', 'bold', 'italic', 'strike', '|',
  'list', 'ordered-list', 'check', '|',
  'quote', 'line', 'code', 'inline-code', '|',
  'table', 'link', '|',
  'undo', 'redo',
]

async function create() {
  const { default: Vditor } = await import('vditor')
  // 样式随实例一起（只在真正进编辑态时才加载 47KB CSS，不进首屏）
  await import('vditor/dist/index.css')
  if (disposed || !host.value) return

  destroy()

  const instance = new Vditor(host.value, {
    // 资源自托管：Lute / 图标 / i18n / KaTeX / 代码主题 全从本地静态目录取
    cdn: '/vditor',
    mode: props.mode,
    value: props.modelValue || '',
    height: props.height,
    minHeight: 240,
    placeholder: '在这里写 Markdown…',
    lang: 'zh_CN',
    icon: 'ant',
    theme: isDark.value ? 'dark' : 'classic',
    counter: { enable: true, type: 'markdown' },
    // 关缓存：同一组件要编辑不同文档，恢复旧内容 = 数据事故（见文件头注释）
    cache: { enable: false },
    toolbar: TOOLBAR,
    toolbarConfig: { pin: true },
    preview: {
      theme: { current: isDark.value ? 'dark' : 'light' },
      hljs: { style: isDark.value ? 'github-dark' : 'github' },
      math: { engine: 'KaTeX' },
      // 编辑预览里不跑 mermaid/echarts 那类重引擎（未自托管，且本项目用不到）
      markdown: { toc: true },
    },
    input: (value) => emit('update:modelValue', value),
    after: () => {
      ready.value = true
      vditor.value = instance
      emit('ready')
    },
  })

  // after 回调可能要等 Lute 脚本就绪，先把引用存下来供 destroy 用
  vditor.value = instance
}

function destroy() {
  try {
    vditor.value?.destroy()
  } catch {
    // 实例可能还没初始化完（Lute 未就绪时 destroy 会抛）——卸载路径上不值得冒泡
  }
  vditor.value = null
  ready.value = false
}

/**
 * 外部内容变化（如切换小节）→ 同步进编辑器。
 * 只在**不等**时写，否则每次输入都会 setValue 一遍，光标会跳。
 */
watch(() => props.modelValue, (val) => {
  const inst = vditor.value
  if (!inst || !ready.value) return
  if (inst.getValue() !== (val || '')) inst.setValue(val || '', true)
})

/** 模式切换：Vditor 没有运行时 setMode，只能重建实例（内容带过去） */
watch(() => props.mode, () => {
  if (ready.value) create()
})

/** 主题切换：Vditor 有 setTheme，不必重建 */
watch(isDark, (dark) => {
  vditor.value?.setTheme(dark ? 'dark' : 'classic',
    dark ? 'dark' : 'light',
    dark ? 'github-dark' : 'github',
    '/vditor/dist/css/content-theme')
})

function focus() {
  vditor.value?.focus()
}

defineExpose({ focus })

// 挂载时创建、卸载时销毁（Vditor 持有全局事件与 DOM，漏掉会泄漏）
onMounted(create)
onBeforeUnmount(() => {
  disposed = true
  destroy()
})
</script>

<template>
  <div class="doc-editor">
    <div ref="host" class="doc-editor-host"></div>
    <p v-if="!ready" class="doc-editor-loading">正在加载编辑器…</p>
  </div>
</template>

<style scoped>
.doc-editor {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
  display: flex;
}
.doc-editor-host {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}
.doc-editor-loading {
  position: absolute;
  inset: 0;
  margin: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: var(--color-text-tertiary);
  pointer-events: none;
}

/* Vditor 自带皮肤是白底直角，这里让它融进设计系统（圆角 / 边框 / 字体 / 主题色）。
   用 :deep 因为 DOM 在运行时由 vditor 生成，不在本组件的模板里。 */
.doc-editor :deep(.vditor) {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-secondary);
  height: 100% !important;
  font-family: inherit;
}
.doc-editor :deep(.vditor-toolbar) {
  border-bottom: 1px solid var(--color-border);
  background: var(--color-bg-tertiary);
  padding: 3px 6px;
}
.doc-editor :deep(.vditor-toolbar__item button) {
  color: var(--color-text-secondary);
}
.doc-editor :deep(.vditor-toolbar__item button:hover),
.doc-editor :deep(.vditor-toolbar__item button.vditor-menu--current) {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}
.doc-editor :deep(.vditor-reset) {
  font-family: inherit;
  font-size: var(--detail-font-size, 14px);
  color: var(--color-text-primary);
  line-height: 1.78;
}
.doc-editor :deep(.vditor-sv),
.doc-editor :deep(.vditor-ir pre.vditor-reset) {
  font-family: 'Consolas', 'Courier New', monospace;
}
.doc-editor :deep(.vditor-counter) {
  color: var(--color-text-tertiary);
}
/* 深色：vditor 自己的 vditor--dark 只覆盖部分底色，这里把边角补齐 */
.doc-editor :deep(.vditor--dark .vditor-toolbar) {
  background: var(--color-bg-tertiary);
}
</style>
