<script setup>
/**
 * KbView.vue — 知识库（活动栏「知识库」入口）
 *
 * 布局 = 「左窄栏文件树 + 右侧大阅读/编辑区」，与图谱页右侧那个窄栏 KbPanel 共用同一份
 * 目录树组件，但职责不同：图谱页那侧是「边看图边挑资料」（单击选中、双击弹只读预览弹窗），
 * 这里是「集中阅读与修改资料」（单击即送正文到右侧常驻工作区，可直接编辑保存）。
 *
 * 为什么不再用 `.page-scroll`：那是「整页纵向滚动」的内容页骨架（设置/仪表盘用它）。
 * 阅读器要的是**两栏各自滚动、外壳不动**——长讲义滚到底时左侧文件树不该跟着跑掉。
 * 所以这里改成 `height: 100%` 的 flex 行。
 *
 * 右侧工作区是**数据源无关**的 `DocWorkspace`（与节点详情共用同一个组件）：本页只
 * 提供三个适配器函数（读正文 / 存正文 / 取原件），渲染与编辑交互全在组件里。
 * 三者的差别就是"知识库文件"与"知识节点小节"的全部差别。
 *
 * 「在图谱中显示」的副作用（切视图 + 高亮）属 HomeView，本组件只转发意图。
 */
import { computed, ref } from 'vue'
import KbPanel from '../components/KbPanel.vue'
import DocWorkspace from '../components/DocWorkspace.vue'
import SidePanel from '../components/SidePanel.vue'
import { getKbNodeRaw, getKbNodeText, saveKbNodeText } from '../api/kb.js'

const emit = defineEmits(['show-in-graph', 'open-lecture'])

/** 左栏宽度：与图谱页共用同一个 storage key 会让两处互相改宽度，故用独立的 kb-page */
const treeWidth = ref(280)
const treeCollapsed = ref(false)

const openedDoc = ref(null)
const workspaceRef = ref(null)

/**
 * 读正文。`doc.full` 由工作区在「进入编辑」时置上 —— 编辑必须拿全文，
 * 预览仍用后端默认的 20K 截断（大文件进编辑不卡）。
 */
async function loadText(doc) {
  const { data } = await getKbNodeText(doc.id, !!doc.full)
  return {
    text: data?.markdown || '',
    totalChars: data?.total_chars ?? 0,
    truncated: !!data?.truncated,
    editable: !!data?.editable,
  }
}

async function saveText(doc, text) {
  await saveKbNodeText(doc.id, text)
}

/** 取原件字节（PDF/Word/Excel/图片真预览）；历史文件没有原件 → 抛 404，工作区静默回落正文 */
async function loadRaw(doc) {
  const { data } = await getKbNodeRaw(doc.id)
  return data
}

/**
 * 切换文件前先问编辑区「脏不脏」——未保存的修改不能因为点一下左侧就没了。
 * 判断在子组件里（它持有 editText/originalText），本组件只负责"要不要切"。
 */
function handleOpenFile(doc) {
  if (openedDoc.value?.id === doc.id) return
  if (workspaceRef.value?.canLeave?.() === false) return
  openedDoc.value = doc
}

/**
 * 打开的是不是「结构化讲义」（目录树里带 `lecture: true`）。
 * 只在此时显示「看讲义」——普通上传的 md 没有时间轴，给入口只会点出 404。
 */
const isLecture = computed(() => openedDoc.value?.lecture === true)
</script>

<template>
  <div class="kbv">
    <SidePanel
      side="left"
      storage-key="kb-page"
      label="文件列表"
      :width="treeWidth"
      :min="220"
      :max="420"
      v-model:width="treeWidth"
      v-model:collapsed="treeCollapsed"
    >
      <KbPanel page-mode @show-in-graph="emit('show-in-graph', $event)" @open-file="handleOpenFile" />
    </SidePanel>

    <DocWorkspace
      ref="workspaceRef"
      :doc="openedDoc"
      :load-text="loadText"
      :save-text="saveText"
      :load-raw="loadRaw"
      :lecture="isLecture"
      empty-title="从左侧选择一个文件开始阅读"
      empty-hint="Markdown / 纯文本可以直接在右侧编辑并保存"
      @open-lecture="(doc) => emit('open-lecture', { docId: doc.id, title: doc.name })"
    />
  </div>
</template>

<style scoped>
/* 两栏：左栏由 SidePanel 自己的宽度控制，右栏吃掉剩余宽度。
   与另外三处侧栏同一标准：贴边、满高、不留外边距/圆角，只用一条 1px 线分隔。 */
.kbv {
  height: 100%;
  display: flex;
  align-items: stretch;
  background: var(--color-bg-page);
  overflow: hidden;
}

.kbv :deep(.doc-ws) {
  border: none;
  border-radius: 0;
}
</style>
