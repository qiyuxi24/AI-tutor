<template>
  <div class="kb-panel">
    <!-- 顶部工具栏：看图时变成「返回 + 文件名」。
         侧栏本来就窄，另起一行放返回键会白白吃掉正文高度，所以复用这一行。 -->
    <div class="kb-toolbar">
      <template v-if="inlineFile">
        <button type="button" class="kb-back-btn" title="返回文件列表（Esc）" @click="closeInlineFile">
          <AppIcon name="chevronLeft" :size="13" />
          返回
        </button>
        <span class="kb-file-name" :title="inlineFile.name">{{ inlineFile.name }}</span>
      </template>
      <template v-else>
        <div class="kb-title">知识库</div>
        <div class="kb-actions">
          <el-tooltip content="新建文件夹" placement="top">
            <button class="kb-icon-btn" @click="openCreateFolder">
              <AppIcon name="folder" :size="16" />
            </button>
          </el-tooltip>
          <el-tooltip content="上传文件" placement="top">
            <label class="kb-icon-btn" :class="{ 'is-busy': uploading }">
              <AppIcon name="paperclip" :size="16" />
              <input ref="fileInputRef" type="file" hidden multiple :accept="acceptTypes" @change="handleUpload">
            </label>
          </el-tooltip>
        </div>
      </template>
    </div>

    <!-- 图谱页：单击 / 双击文件 → **在侧栏内直接展示**，不再弹只读弹窗。
         独立页（pageMode）不走这里：那边右侧本来就是常驻工作区，本组件只发 open-file。
         用同一个 DocWorkspace，所以这里看到的样式与阅读体验与独立页、节点详情完全一致；
         能编辑就顺带能编辑（.md/.markdown/.txt，后端同一套校验）。 -->
    <div v-if="inlineFile" class="kb-file-view">
      <DocWorkspace
        ref="inlineRef"
        bare
        :doc="inlineFile"
        :load-text="loadInlineText"
        :save-text="saveInlineText"
        :load-raw="loadInlineRaw"
      />
    </div>

    <!-- 列表区用 v-show 而非 v-if：看图时 el-tree 只是隐藏、不被销毁，
         点「返回」能回到原来的展开/勾选状态（v-if 会把展开的文件夹全部折叠回去）。
         外层 div 承担原来 template 直接当 flex 子项的职责，所以要补 flex 纵向布局。 -->
    <div v-show="!inlineFile" class="kb-list-view">
    <!-- 上传进度：串行上传+向量化是分钟级的，必须有常驻提示（ElMessage 3 秒就没了） -->
    <div v-if="uploading" class="kb-uploading">{{ uploadStatus }}</div>

    <!-- 一键生成学科图谱：勾选下方文件 / 文件夹作为书籍来源 -->
    <div class="kb-gen">
      <el-input
        v-model="subjectInput"
        placeholder="学科名，如：数据结构"
        size="small"
        maxlength="100"
        @keyup.enter="handleGenerate"
      />
      <el-button
        type="primary"
        size="small"
        :loading="submitting || !!store.graphGenerating"
        :disabled="!tree.length || (!!store.graphGenerating && !submitting)"
        @click="handleGenerate"
      >
        {{ store.graphGenerating ? `生成中（${store.graphGenerating}）…` : '生成图谱' }}
      </el-button>
    </div>

    <!-- 建图进度：分钟级后台任务，只有"生成中…"用户不知道还要等多久（TODO §11.4-B）。
         后端 progress 是**增量补全**口径（已有学科会并轨，百分比不从 0 开始）
         → 必须同时给 filled/total 绝对数，否则 60% 看着像"快完了"其实才刚开始。 -->
    <div v-if="store.graphBuildProgress" class="kb-gen-progress">
      <el-progress
        :percentage="store.graphBuildProgress.percent || 0"
        :stroke-width="6"
        :show-text="false"
      />
      <span class="kb-gen-progress-text">
        {{ store.graphBuildProgress.filled || 0 }} / {{ store.graphBuildProgress.total || 0 }} 个知识点
      </span>
    </div>

    <!-- 目录树：勾选 = 生成来源；条目三点 = 单个文件的操作 -->
    <div class="kb-tree-wrap" v-if="tree.length">
      <el-tree
        ref="treeRef"
        :data="tree"
        node-key="id"
        :props="treeProps"
        :default-expand-all="false"
        :expand-on-click-node="false"
        show-checkbox
        highlight-current
        @node-click="handleNodeClick"
        @node-dblclick="handleNodeDblClick"
      >
        <template #default="{ data }">
          <div
            class="kb-node"
            :class="{ selected: selectedNode?.id === data.id }"
          >
            <span class="kb-node-icon">
              <AppIcon :name="data.type === 'folder' ? 'folder' : 'fileText'" :size="16" />
            </span>
            <span class="kb-node-name">{{ data.name }}</span>
            <ItemMoreButton title="更多操作" @click="openMenuAt($event, 'kb', data)" />
          </div>
        </template>
      </el-tree>
    </div>
    <div v-else class="kb-empty">
      <p>还没有文件</p>
    </div>

    <!-- 索引统计 -->
    <div v-if="stats.files" class="kb-stats">
      {{ stats.files }} 个文件 · {{ stats.chunks }} 个向量分块
    </div>
    </div>

    <!-- 条目菜单：单个文件 / 文件夹的操作 -->
    <ContextMenu :visible="menuVisible" :x="menuX" :y="menuY" @close="closeMenu">
      <template #default="{ close }">
        <template v-if="menuNode">
          <template v-if="menuNode.type === 'file'">
            <div class="menu-item" @click="close(); emitShowInGraph(menuNode)">
              <span class="menu-icon">
                <AppIcon name="focus" :size="14" />
              </span>
              在图谱中显示
            </div>
            <div class="menu-item" @click="close(); openFromMenu(menuNode)">
              <span class="menu-icon">
                <AppIcon name="eye" :size="14" />
              </span>
              预览
            </div>
            <div class="menu-item" @click="close(); openInBrowser(menuNode)">
              <span class="menu-icon">
                <AppIcon name="externalLink" :size="14" />
              </span>
              在浏览器中打开
            </div>
          </template>
          <template v-else>
            <div class="menu-item" @click="close(); openCreateFolder(menuNode)">
              <span class="menu-icon">
                <AppIcon name="folderPlus" :size="14" />
              </span>
              新建子文件夹
            </div>
            <div class="menu-item" @click="close(); uploadInto(menuNode)">
              <span class="menu-icon">
                <AppIcon name="upload" :size="14" />
              </span>
              上传到此文件夹
            </div>
          </template>
          <div class="menu-divider"></div>
          <div class="menu-item menu-item-danger" @click="close(); confirmDelete(menuNode)">
            <span class="menu-icon">
              <AppIcon name="trash" :size="14" />
            </span>
            删除{{ menuNode.type === 'folder' ? '文件夹（含内容）' : '文件' }}
          </div>
        </template>
      </template>
    </ContextMenu>

    <!-- 新建文件夹弹窗 -->
    <el-dialog v-model="showCreateDialog" title="新建文件夹" width="320px">
      <el-input v-model="newFolderName" placeholder="文件夹名称" maxlength="100" @keyup.enter="createFolder" />
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="createFolder">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
/**
 * KbPanel.vue — 知识库侧栏（知识图谱页右侧，展开/收起由 HomeView 的容器控制）
 *
 * 职责：
 *   - 目录树：建文件夹 / 上传 / 勾选（生成来源）/ 条目三点菜单（预览 · 在浏览器中打开 · 删除）
 *   - 顶部一键生成：勾选文件或文件夹 → 输入学科名 → 生成该学科知识图谱
 *
 * 这里**没有**"加入对话上下文"的入口（2026-11-16 删）：引用资料/知识点只有对话输入区
 * 那个 `+` 一个入口（`InputArea.vue` + `ContextPicker.vue`）。选区状态本来就只有一份
 * （`conv.selection`），多几个入口只是让同一件事看起来像几件事。
 *
 * 数据流：本组件直接读写 store（与 GraphSubjectBar 同款），
 * 不向父级转发动作——HomeView 只管它的展开与收起。
 *
 * 为什么文件操作收在三点菜单里：侧栏窄，逐个平铺按钮既挤压文件名又易误触，
 * 一个常驻三点按钮足够。
 *
 * 看文件正文的两种形态（2026-11-15 起）：
 *   - **图谱页（默认）**：单击文件即在**本侧栏内**展开阅读/编辑区（`DocWorkspace`，
 *     与知识库独立页、节点详情同一个组件），点「返回」回到目录树。
 *     不再弹 `KbTextPreviewDialog` 模态 —— 侧栏本身就有空间，弹窗还会挡住背后的图谱，
 *     而这页的用途恰恰是"边看图边翻资料"。
 *   - **知识库独立页（pageMode）**：只发 `open-file` 事件，正文交给右侧常驻工作区。
 *     （那里右侧的屏比这里宽得多，塞进窄栏反而更差。）
 */
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { confirmAction } from '../utils/feedback'
import {
  getKbTree,
  createKbFolder,
  uploadKbFile,
  deleteKbNode,
  getKbNodeText,
  getKbNodeRaw,
  getKbStats,
  saveKbNodeText,
} from '../api/kb.js'
import DocWorkspace from './DocWorkspace.vue'
import ContextMenu from './ContextMenu.vue'
import ItemMoreButton from './ItemMoreButton.vue'
import { useContextMenu } from '../utils/contextMenu.js'
import { useChatStore } from '../stores/chatStore.js'
import AppIcon from './AppIcon.vue'

const store = useChatStore()

/* 「在图谱中显示」要切图谱视图并驱动画布高亮 —— 那是 HomeView 的职责，本组件只发意图 */
const emit = defineEmits(['show-in-graph', 'open-file'])

/**
 * pageMode：知识库独立页的左栏形态。
 * 独立页把「读正文」交给右侧常驻工作区（DocWorkspace），本组件退化成纯目录树 ——
 * 单击文件即发 `open-file`。图谱页（默认）没有第二块屏，所以正文就在本侧栏内展开
 * （见文件头的「看文件正文的两种形态」）。
 */
const props = defineProps({
  pageMode: { type: Boolean, default: false },
})

const treeRef = ref(null)
const fileInputRef = ref(null)
const tree = ref([])
const selectedNode = ref(null)
const showCreateDialog = ref(false)
const newFolderName = ref('')
const creating = ref(false)

// 索引统计（文件数 / 向量分块数）
const stats = ref({ files: 0, chunks: 0 })

// 一键生成
const subjectInput = ref('')
// 本地"请求在途"（HTTP 层）；跨页面/超时的真正状态是 store.graphGenerating
const submitting = ref(false)

// 上传状态：uploading 驱动进度行与入口置灰，uploadStatus 显示「正在上传 i/N：文件名」
const uploading = ref(false)
const uploadStatus = ref('')

// 条目菜单：由常驻三点按钮触发；targetData = 该目录节点
const {
  visible: menuVisible,
  x: menuX,
  y: menuY,
  targetData: menuNode,
  openAt: openMenuAt,
  close: closeMenu,
} = useContextMenu()

/* 侧栏内联阅读/编辑区（图谱页）：非空 = 正在看这个文件，目录树让位给正文。
   与独立页共用同一个 DocWorkspace，所以三个适配器函数与 KbView 里那三个同义。 */
const inlineFile = ref(null)
const inlineRef = ref(null)

/** 读正文：`doc.full` 由工作区在进入编辑时置上（编辑必须拿全文，预览保持默认截断） */
async function loadInlineText(doc) {
  const { data } = await getKbNodeText(doc.id, !!doc.full)
  return {
    text: data?.markdown || '',
    totalChars: data?.total_chars ?? 0,
    truncated: !!data?.truncated,
    editable: !!data?.editable,
  }
}

async function saveInlineText(doc, text) {
  await saveKbNodeText(doc.id, text)
}

/** 原件字节（PDF/Word/Excel/图片真预览）；历史文件 404 → 工作区静默回落正文 */
async function loadInlineRaw(doc) {
  const { data } = await getKbNodeRaw(doc.id)
  return data
}

const treeProps = {
  children: 'children',
  label: 'name',
}

// 与后端 parsers 包支持的格式保持一致
const acceptTypes = '.pdf,.docx,.pptx,.md,.markdown,.txt,.png,.jpg,.jpeg,.bmp,.webp,.tiff,.gif,.csv,.json,.log,.py,.js,.ts,.html,.xml,.epub,.fb2,.mobi,.azw,.azw3,.djvu,.srt,.vtt'

async function loadTree() {
  try {
    const res = await getKbTree()
    tree.value = res.data.tree || []
  } catch (e) {
    ElMessage.error('加载知识库失败：' + (e.response?.data?.detail || e.message))
  }
}

async function loadStats() {
  try {
    const res = await getKbStats()
    stats.value = { files: res.data?.files ?? 0, chunks: res.data?.chunks ?? 0 }
  } catch {
    // 统计只是装饰，失败静默
  }
}

function handleNodeClick(data) {
  selectedNode.value = data
  // 单击文件就看正文：独立页送到右侧工作区，图谱页在本侧栏内展开（见文件头两种形态）
  if (data?.type === 'file') openFile(data)
}

/** 双击 = 与单击同一动作（保留双击是因为"双击打开"是通用习惯，不该失效） */
function handleNodeDblClick(data) {
  if (data?.type !== 'file') return
  openFile(data)
}

/**
 * 打开一个文件看正文（单击 / 双击 / 菜单「预览」三个入口共用这一条）。
 *
 * 已开着同一个文件时直接返回 —— DocWorkspace 的 watch 是按 id 触发的，重复赋值
 * 会白重读一遍正文；而切到别的文件前必须问一句「脏不脏」（编辑中的修改不能丢）。
 */
function openFile(data) {
  if (!data || data.type !== 'file') return
  selectedNode.value = data
  if (props.pageMode) {
    emit('open-file', data)
    return
  }
  if (inlineFile.value?.id === data.id) return
  if (inlineFile.value && inlineRef.value?.canLeave?.() === false) return
  inlineFile.value = data
}

/** 返回目录树（编辑中有未保存修改时先确认） */
function closeInlineFile() {
  if (inlineRef.value?.canLeave?.() === false) return
  inlineFile.value = null
}

/**
 * 菜单「在图谱中显示」：把「这份资料影响了哪些知识图谱节点」交给 HomeView
 * （切图谱视图 + 高亮 + 选中该文件的小节/题目）。
 * 注意 `data.id` 就是 KB 的 file 节点 id，也就是来源条目里的 `doc_id`（同一个命名空间）。
 */
function emitShowInGraph(data) {
  if (!data || data.type !== 'file') return
  emit('show-in-graph', { docId: data.id, docName: data.name })
}

/** 菜单「预览」：与单击同一动作（两个入口一份意图） */
function openFromMenu(data) {
  openFile(data)
}

/**
 * 菜单「在浏览器中打开」：把原件交给浏览器原生处理（PDF/图片 直接内置阅读器，
 * 其余格式浏览器能认的认、认不了的（docx/pptx）自动下载）—— 比在弹窗里渲染省事得多。
 *
 * 开窗必须**同步**先做：await 之后用户手势已过期，再 window.open 会被弹窗拦截；
 * 拿不到窗口就明确提示，而不是退回 `location.href`（那会把整个应用替换掉）。
 */
async function openInBrowser(data) {
  if (!data || data.type !== 'file') return
  const win = window.open('', '_blank')
  if (!win) {
    ElMessage.warning('浏览器拦截了新标签页，请允许弹出窗口后重试')
    return
  }
  try {
    const res = await getKbNodeRaw(data.id)
    const url = URL.createObjectURL(res.data)
    win.location.href = url
    // ponytail: 60s 后回收，够新标签页读完；长会话反复开大文件会各留一份 blob，不回收就是显存泄漏
    setTimeout(() => URL.revokeObjectURL(url), 60000)
  } catch (e) {
    win.close()
    ElMessage.warning(e.response?.status === 404
      ? '该文件没有原件（历史数据），无法在浏览器中打开'
      : (e.message || '打开失败'))
  }
}

/** 新建文件夹：传 folder 则作为其子目录（条目菜单），否则用当前选中节点 */
function openCreateFolder(folder = null) {
  if (folder) selectedNode.value = folder
  newFolderName.value = ''
  showCreateDialog.value = true
}

async function createFolder() {
  const name = newFolderName.value.trim()
  if (!name) {
    ElMessage.warning('请输入文件夹名称')
    return
  }
  creating.value = true
  try {
    const parentId = selectedNode.value?.type === 'folder' ? selectedNode.value.id : null
    await createKbFolder(name, parentId)
    ElMessage.success('文件夹已创建')
    showCreateDialog.value = false
    loadTree()
  } catch (e) {
    ElMessage.error('创建失败：' + (e.response?.data?.detail || e.message))
  } finally {
    creating.value = false
  }
}

/** 菜单「上传到此文件夹」：选中它再走同一条上传路径（父目录取 selectedNode） */
function uploadInto(folder) {
  selectedNode.value = folder
  fileInputRef.value?.click()
}

async function handleUpload(event) {
  const files = Array.from(event.target.files || [])
  if (!files.length) return
  if (uploading.value) return          // 防重复：上一批还没传完（入口同时置灰）
  const parentId = selectedNode.value?.type === 'folder' ? selectedNode.value.id : null

  // 串行上传（后端解析 + 向量化，单个文件就可能几十秒）→ 进度必须常驻在面板里
  uploading.value = true
  try {
    for (let i = 0; i < files.length; i++) {
      uploadStatus.value = `正在上传 ${i + 1}/${files.length}：${files[i].name}`
      await uploadKbFile(files[i], parentId)
    }
    ElMessage.success(`成功上传 ${files.length} 个文件`)
    loadTree()
    loadStats()
  } catch (e) {
    ElMessage.error('上传失败：' + (e.response?.data?.detail || e.message))
  } finally {
    uploading.value = false
    uploadStatus.value = ''
    event.target.value = ''
  }
}

// 后台任务完成后由 SSE 递增 `kbEpoch` → 重新拉目录树（导入是分钟级，请求响应早结束了）
watch(() => store.kbEpoch, () => {
  loadTree()
  loadStats()
})

/** 勾选（含半选）的文件/文件夹 ID —— 文件夹由后端自动展开为其中所有文件 */
function getCheckedIds() {
  const checked = treeRef.value?.getCheckedKeys() || []
  const half = treeRef.value?.getHalfCheckedKeys() || []
  return [...new Set([...checked, ...half])]
}

async function handleGenerate() {
  const subject = subjectInput.value.trim()
  if (!subject) {
    ElMessage.warning('请输入学科名（如"数据结构"）')
    return
  }
  const nodeIds = getCheckedIds()
  if (!nodeIds.length) {
    ElMessage.warning('请先勾选至少一个文件或文件夹作为书籍来源')
    return
  }
  // 建图是**分钟级后台任务**：这里只负责发出请求。"建完了"由 store 的
  // graph_updated 通知收口（后端 finally 里 notify 落库再推），所以：
  //   · 按钮状态读 store.graphGenerating —— 请求超时也不会误以为结束，
  //     切页面/刷新回来仍能看到"生成中"（这是最容易被误认为"卡死"的地方）；
  //   · 结果提示（成功/失败/未入图资料）统一由 store 弹，组件不再重复弹一遍。
  submitting.value = true
  try {
    await store.generateSubjectGraph(subject, nodeIds)
  } finally {
    submitting.value = false       // 释放 HTTP 层；真正的"生成中"是 store 那个
  }
}

async function confirmDelete(data) {
  const ok = await confirmAction(
    data.type === 'folder'
      ? `确定删除文件夹「${data.name}」及其所有内容吗？此操作不可撤销。`
      : `确定删除文件「${data.name}」吗？此操作不可撤销。`
  )
  if (!ok) return
  try {
    await deleteKbNode(data.id)
    ElMessage.success('已删除')
    // 被删的如果还在对话选区里，得一并摘掉 —— 否则下一条消息会带着一个已不存在的
    // node_id 过去（后端 collect_files 返回空 → 选区整体变成空，模型什么依据都没有）。
    const sel = store.selection
    if (sel?.docs?.some(d => d.id === data.id)) {
      store.toggleSelectionItem('docs', { id: data.id })
    }
    loadTree()
    loadStats()
    // 正在看的就是被删的那个文件 → 退回列表（否则正文区停在已不存在的节点上）
    if (inlineFile.value?.id === data.id) inlineFile.value = null
  } catch (e) {
    ElMessage.error('删除失败：' + (e.response?.data?.detail || e.message))
  }
}

/**
 * Esc 返回目录树。
 * 挂在 window 上而不是面板上：焦点通常在正文/编辑器里，事件冒泡不到面板容器。
 * 编辑器正在用时让给它 —— Vditor 自己也吃 Esc（退全屏编辑、关浮层）。
 */
function handleKeydown(e) {
  if (e.key !== 'Escape' || !inlineFile.value) return
  if (document.activeElement?.closest?.('.vditor')) return
  closeInlineFile()
}

onMounted(() => {
  loadTree()
  loadStats()
  window.addEventListener('keydown', handleKeydown)
})
onUnmounted(() => window.removeEventListener('keydown', handleKeydown))
</script>

<style scoped>
.kb-panel {
  display: flex;
  flex-direction: column;
  /* basis auto：卡片高度随内容自适应（父级 SidePanel 不给固定高度） */
  flex: 1 1 auto;
  min-height: 0;
  background: transparent;
  overflow: hidden;
}

.kb-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  /* 右侧让开折叠按钮（知识库栏是右栏，按钮在右边）；左栏下 --sp-head-pad-r 为 0，max() 回落 12px */
  padding: 12px max(12px, var(--sp-head-pad-r, 0px)) 8px max(12px, var(--sp-head-pad-l, 0px));
  flex-shrink: 0;
}

.kb-title {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: var(--color-text-primary);
}

.kb-actions {
  display: flex;
  gap: 4px;
}

.kb-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: background 0.2s;
}
.kb-icon-btn:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}
/* 上传中：入口置灰不可点（防反复点击），视觉上也说明"这活儿还在跑" */
.kb-icon-btn.is-busy {
  opacity: 0.4;
  pointer-events: none;
}

/* 上传进度（常驻，不随 toast 消失） */
.kb-uploading {
  padding: 6px 12px;
  font-size: var(--font-size-xs);
  color: var(--color-accent);
  flex-shrink: 0;
}

/* ── 侧栏内联阅读/编辑区（图谱页单击文件） ──
   工具栏那一行已被复用成「返回 + 文件名」（见模板），所以这里只让正文吃掉剩余高度。
   DocWorkspace 用 bare 模式：外框与底色由本面板给，别在窄栏里再嵌一层卡片。 */
.kb-file-view {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* 列表区（上传 / 生成 / 目录树 / 上下文 / 统计）：看图时整体隐藏但**不销毁**。
   这些子块原本直接挂在 .kb-panel 的 flex 列里，包了一层就得把同样的布局补在包装上。 */
.kb-list-view {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.kb-back-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  flex-shrink: 0;
  padding: 3px 8px 3px 5px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: var(--font-size-xs);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.kb-back-btn:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

/* 文件名：窄栏里唯一的标题，超出用省略号（完整名在 title 里） */
.kb-file-name {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--color-text-primary);
}

/* 窄栏里的编辑器：Vditor 工具栏按钮多，560px 下会换行成两三排，
   这里把它压成单排横向滚动（比让它撑高、把正文挤没了好）。 */
.kb-file-view :deep(.vditor-toolbar) {
  flex-wrap: nowrap;
  overflow-x: auto;
  overflow-y: hidden;
}
.kb-file-view :deep(.vditor-toolbar::-webkit-scrollbar) {
  height: 0;
}

/* 窄栏的呼吸感：DocWorkspace 的默认左右留白（24px）是按独立页的大屏定的，
   在 240–560px 的侧栏里会把正文挤成窄条，这里收一半。 */
.kb-file-view :deep(.doc-pane) {
  padding: 12px 14px 24px;
}
.kb-file-view :deep(.doc-edit) {
  padding: 6px 8px 10px;
}
.kb-file-view :deep(.doc-docx),
.kb-file-view :deep(.doc-sheet) {
  padding: 10px 12px 20px;
}
.kb-file-view :deep(.doc-state) {
  padding: 8px 14px;
}
/* 编辑动作行（bare 头部）在窄栏里不需要上下的额外留白与分隔线 ——
   文件名那一行（本面板的工具栏）已经提供了视觉分隔。 */
.kb-file-view :deep(.doc-head--bare) {
  padding: 0 0 6px 0;
  border-bottom: none;
}

/* ── 一键生成区（侧栏顶部） ── */
.kb-gen {
  display: flex;
  gap: 6px;
  padding: 0 10px;
  flex-shrink: 0;
}
.kb-gen :deep(.el-input) {
  flex: 1;
  min-width: 0;
}
.kb-gen :deep(.el-button) {
  flex-shrink: 0;
}
/* 建图进度条（常驻，随轮询更新；建图结束由 store 清空） */
.kb-gen-progress {
  padding: 2px 12px 6px;
  flex-shrink: 0;
}
.kb-gen-progress-text {
  display: block;
  margin-top: 4px;
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
}
.kb-gen-hint {
  margin: 6px 12px 8px;
  font-size: var(--font-size-xs);
  line-height: 1.5;
  color: var(--color-text-tertiary);
  flex-shrink: 0;
}

.kb-tree-wrap {
  flex: 1 1 auto;   /* 同上：内容少时按内容高度，封顶后才吃剩余空间并滚动 */
  overflow-y: auto;
  padding: 0 6px 6px;
  min-height: 0;
}
.kb-tree-wrap::-webkit-scrollbar { width: 4px; }
.kb-tree-wrap::-webkit-scrollbar-thumb {
  background: var(--color-border-light);
  border-radius: var(--radius-sm);
}

.kb-node {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  border-radius: var(--radius-md);
  cursor: pointer;
  /* ⚠️ 不能写 width:100%：el-tree 的 __content 里还有缩进 / 展开箭头 / 复选框，
     它按 content 的 100% 计算就会比可用宽度多出一截，长文件名时把右侧 ⋯ 按钮
     顶出可视区（表现为"三点被盖住、点不到"）。flex:1 + min-width:0 才是
     "占满剩余宽度、超长时收缩省略"的正确写法。 */
  flex: 1 1 auto;
  min-width: 0;
}
.kb-node:hover {
  background: var(--color-bg-hover);
}
.kb-node.selected {
  background: var(--color-accent-light);
}

.kb-node-icon {
  display: flex;
  align-items: center;
  color: var(--color-yellow);
}
.kb-node-icon svg {
  color: var(--color-yellow);
}
.kb-node-name {
  flex: 1 1 0;
  min-width: 0;
  font-size: 13px;
  color: var(--color-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 三点按钮样式已收进 ItemMoreButton.vue（与学科栏 / 对话列表共用一份） */

.kb-empty {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 20px;
  color: var(--color-text-tertiary);
  font-size: 13px;
  text-align: center;
}

.kb-stats {
  padding: 6px 12px 10px;
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
  flex-shrink: 0;
}

/* Element Plus 深色适配 */
.kb-panel :deep(.el-tree) {
  background: transparent;
  --el-tree-node-hover-bg-color: var(--color-bg-hover);
  --el-tree-text-color: var(--color-text-primary);
}
.kb-panel :deep(.el-tree-node__content) {
  height: 32px;
  /* 允许行内容收缩：默认 min-width:auto 会按「文件名全文」撑开，行宽就溢出 */
  min-width: 0;
  padding-right: 2px;
}
.kb-panel :deep(.el-tree-node__expand-icon) {
  color: var(--color-text-tertiary);
}
.kb-panel :deep(.el-checkbox) {
  margin-right: 2px;
}
</style>
