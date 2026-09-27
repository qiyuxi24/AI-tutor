<template>
  <div class="kb-panel">
    <!-- 顶部工具栏 -->
    <div class="kb-toolbar">
      <div class="kb-title">知识库</div>
      <div class="kb-actions">
        <el-tooltip content="新建文件夹" placement="top">
          <button class="kb-icon-btn" @click="openCreateFolder">
            <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>
          </button>
        </el-tooltip>
        <el-tooltip content="上传文件" placement="top">
          <label class="kb-icon-btn">
            <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M16.5 6v11.5c0 2.21-1.79 4-4 4s-4-1.79-4-4V5a2.5 2.5 0 0 1 5 0v10.5c0 .55-.45 1-1 1s-1-.45-1-1V6H10v9.5a2.5 2.5 0 0 0 5 0V5c0-2.21-1.79-4-4-4S7 2.79 7 5v12.5c0 3.04 2.46 5.5 5.5 5.5s5.5-2.46 5.5-5.5V6h-1.5z"/></svg>
            <input ref="fileInputRef" type="file" hidden multiple :accept="acceptTypes" @change="handleUpload">
          </label>
        </el-tooltip>
      </div>
    </div>

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
        :loading="generating"
        :disabled="!tree.length"
        @click="handleGenerate"
      >
        {{ generating ? '生成中...' : '一键生成图谱' }}
      </el-button>
    </div>
    <p class="kb-gen-hint">勾选下方文件（或文件夹）作为来源，AI 分析内容后生成该学科的知识图谱。</p>

    <!-- 目录树：勾选 = 生成来源；右键 = 单个文件的操作 -->
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
            @contextmenu.prevent="openMenu($event, 'kb', data)"
          >
            <span class="kb-node-icon">
              <svg v-if="data.type === 'folder'" viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>
              <svg v-else viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z"/></svg>
            </span>
            <span class="kb-node-name">{{ data.name }}</span>
          </div>
        </template>
      </el-tree>
    </div>
    <div v-else class="kb-empty">
      <p>暂无文件，点击右上角上传文件或新建文件夹</p>
      <p class="kb-empty-sub">支持 PDF / Word / PPT / Markdown / 代码 / 图片OCR 等</p>
    </div>

    <!-- 当前上下文范围 -->
    <div v-if="contextLabel" class="kb-context">
      <div class="kb-context-label">当前检索范围</div>
      <div class="kb-context-item">
        <span>{{ contextLabel }}</span>
        <button class="kb-clear-btn" @click="clearContext">清除</button>
      </div>
    </div>

    <!-- 索引统计 -->
    <div v-if="stats.files" class="kb-stats">
      {{ stats.files }} 个文件 · {{ stats.chunks }} 个向量分块
    </div>

    <!-- 右键菜单：单个文件 / 文件夹的操作 -->
    <ContextMenu :visible="menuVisible" :x="menuX" :y="menuY" @close="closeMenu">
      <template #default="{ close }">
        <template v-if="menuNode">
          <div class="menu-item" @click="close(); setContext(menuNode)">
            <span class="menu-icon">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 5v14" /><path d="M5 12h14" />
              </svg>
            </span>
            放入对话上下文
          </div>
          <template v-if="menuNode.type === 'file'">
            <div class="menu-item" @click="close(); openPreview(menuNode)">
              <span class="menu-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" /><circle cx="12" cy="12" r="3" />
                </svg>
              </span>
              查看正文
            </div>
          </template>
          <template v-else>
            <div class="menu-item" @click="close(); openCreateFolder(menuNode)">
              <span class="menu-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
                </svg>
              </span>
              新建子文件夹
            </div>
            <div class="menu-item" @click="close(); uploadInto(menuNode)">
              <span class="menu-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" />
                </svg>
              </span>
              上传到此文件夹
            </div>
          </template>
          <div class="menu-divider"></div>
          <div class="menu-item menu-item-danger" @click="close(); confirmDelete(menuNode)">
            <span class="menu-icon">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M3 6h18" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" /><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
              </svg>
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

    <!-- 文件正文预览（只读；采集到的网页原文 / 上传文档的解析文本） -->
    <KbTextPreviewDialog
      v-model:visible="previewVisible"
      :name="preview.name"
      :markdown="preview.markdown"
      :chars="preview.chars"
      :total-chars="preview.totalChars"
      :truncated="preview.truncated"
      :loading="previewLoading"
    />
  </div>
</template>

<script setup>
/**
 * KbPanel.vue — 知识库侧栏（知识图谱页右侧，展开/收起由 HomeView 的容器控制）
 *
 * 职责：
 *   - 目录树：建文件夹 / 上传 / 勾选（生成来源）/ 右键菜单（查看正文 · 放入上下文 · 删除）
 *   - 顶部一键生成：勾选文件或文件夹 → 输入学科名 → 生成该学科知识图谱
 *   - 底部：当前检索范围（写进 chatStore.kbContext，对话据此带 kb_node_ids）
 *
 * 数据流：本组件直接读写 store（与 GraphSubjectBar 同款），
 * 不向父级转发动作——HomeView 只管它的展开与收起。
 *
 * 为什么文件操作放右键而不留 hover 按钮：侧栏窄，三个悬浮按钮既挤压文件名又易误触。
 */
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getKbTree,
  createKbFolder,
  uploadKbFile,
  deleteKbNode,
  getKbContext,
  getKbNodeText,
  getKbStats,
} from '../api/kb.js'
import KbTextPreviewDialog from './KbTextPreviewDialog.vue'
import ContextMenu from './ContextMenu.vue'
import { useContextMenu } from '../utils/contextMenu.js'
import { useChatStore } from '../stores/chatStore.js'

const store = useChatStore()

const treeRef = ref(null)
const fileInputRef = ref(null)
const tree = ref([])
const selectedNode = ref(null)
const showCreateDialog = ref(false)
const newFolderName = ref('')
const creating = ref(false)

// 当前放入上下文的范围（单个文件或文件夹）
const contextNode = ref(null)
const contextLabel = ref('')

// 索引统计（文件数 / 向量分块数）
const stats = ref({ files: 0, chunks: 0 })

// 一键生成
const subjectInput = ref('')
const generating = ref(false)

// 右键菜单：targetData = 被右键的目录节点
const {
  visible: menuVisible,
  x: menuX,
  y: menuY,
  targetData: menuNode,
  open: openMenu,
  close: closeMenu,
} = useContextMenu()

// 正文预览（只读弹窗）
const previewVisible = ref(false)
const previewLoading = ref(false)
const preview = ref({ name: '', markdown: '', chars: 0, totalChars: 0, truncated: false })

const treeProps = {
  children: 'children',
  label: 'name',
}

// 与后端 parsers 包支持的格式保持一致
const acceptTypes = '.pdf,.docx,.pptx,.md,.markdown,.txt,.png,.jpg,.jpeg,.bmp,.webp,.tiff,.gif,.csv,.json,.log,.py,.js,.ts,.html,.xml,.epub,.fb2,.mobi,.azw,.azw3,.djvu'

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
}

/** 双击文件 = 看正文（与图谱里双击节点看详情的习惯一致）；文件夹双击不做事 */
function handleNodeDblClick(data) {
  if (data?.type === 'file') openPreview(data)
}

/** 拉取并弹出文件正文预览（只读；node_id 来自目录树，天然按用户隔离） */
async function openPreview(data) {
  if (!data || data.type !== 'file') return
  preview.value = { name: data.name, markdown: '', chars: 0, totalChars: 0, truncated: false }
  previewVisible.value = true
  previewLoading.value = true
  try {
    const res = await getKbNodeText(data.id)
    const d = res.data || {}
    preview.value = {
      name: d.name || data.name,
      markdown: d.markdown || '',
      chars: d.chars || 0,
      totalChars: d.total_chars || 0,
      truncated: !!d.truncated,
    }
  } catch (e) {
    previewVisible.value = false
    ElMessage.warning(e.response?.data?.detail || e.message || '读取正文失败')
  } finally {
    previewLoading.value = false
  }
}

/** 新建文件夹：传 folder 则作为其子目录（右键菜单），否则用当前选中节点 */
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

/** 右键「上传到此文件夹」：选中它再走同一条上传路径（父目录取 selectedNode） */
function uploadInto(folder) {
  selectedNode.value = folder
  fileInputRef.value?.click()
}

async function handleUpload(event) {
  const files = Array.from(event.target.files || [])
  if (!files.length) return
  const parentId = selectedNode.value?.type === 'folder' ? selectedNode.value.id : null

  ElMessage.info(`正在上传 ${files.length} 个文件，解析并向量化中...`)
  try {
    for (const file of files) {
      await uploadKbFile(file, parentId)
    }
    ElMessage.success(`成功上传 ${files.length} 个文件`)
    loadTree()
    loadStats()
  } catch (e) {
    ElMessage.error('上传失败：' + (e.response?.data?.detail || e.message))
  } finally {
    event.target.value = ''
  }
}

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
  generating.value = true
  try {
    // 走 store（图谱写入的唯一前端入口）：生成后自动刷新学科列表并切到该学科
    const d = (await store.generateSubjectGraph(subject, nodeIds)) || {}
    ElMessage.success(
      `「${subject}」生成完成：新增 ${(d.created_nodes || []).length} 个知识点、${d.created_edges || 0} 条关系`
    )
  } catch (e) {
    ElMessage.error('生成失败：' + (e.response?.data?.detail || e.message))
  } finally {
    generating.value = false
  }
}

/** 把节点设为对话检索范围（写进 store，对话请求据此带 kb_node_ids） */
async function setContext(data) {
  if (!data) return
  try {
    const res = await getKbContext(data.id)
    const fileCount = res.data.file_count
    if (fileCount === 0) {
      ElMessage.warning('该节点下没有文件')
      return
    }
    contextNode.value = data
    contextLabel.value = data.type === 'folder'
      ? `${data.name}/ (${fileCount} 个文件)`
      : data.name
    store.setKbContext({
      nodeIds: res.data.node_ids || [],   // 实际检索的文件节点 ID 列表
      name: data.name,
    })
    ElMessage.success(`已设置检索范围：${contextLabel.value}`)
  } catch (e) {
    ElMessage.error('设置失败：' + (e.response?.data?.detail || e.message))
  }
}

function clearContext() {
  contextNode.value = null
  contextLabel.value = ''
  store.setKbContext(null)
}

async function confirmDelete(data) {
  try {
    await ElMessageBox.confirm(
      data.type === 'folder'
        ? `确定删除文件夹「${data.name}」及其所有内容吗？`
        : `确定删除文件「${data.name}」吗？`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    await deleteKbNode(data.id)
    ElMessage.success('已删除')
    if (contextNode.value?.id === data.id) clearContext()
    loadTree()
    loadStats()
  } catch (e) {
    ElMessage.error('删除失败：' + (e.response?.data?.detail || e.message))
  }
}

onMounted(() => {
  loadTree()
  loadStats()
})
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
  padding: 12px 12px 8px;
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
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: background 0.2s;
}
.kb-icon-btn:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
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
.kb-gen-hint {
  margin: 6px 12px 8px;
  font-size: 11px;
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
  border-radius: 2px;
}

.kb-node {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  border-radius: 6px;
  cursor: pointer;
  width: 100%;
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
  flex: 1;
  font-size: 13px;
  color: var(--color-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

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
.kb-empty-sub {
  font-size: 12px;
  color: var(--color-text-muted);
}

.kb-context {
  padding: 10px 12px;
  border-top: 1px solid var(--color-border);
  flex-shrink: 0;
}
.kb-context-label {
  font-size: 12px;
  color: var(--color-text-tertiary);
  margin-bottom: 6px;
}
.kb-context-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px;
  background: var(--color-green-light);
  border-radius: 6px;
  font-size: 13px;
  color: var(--color-green);
}
.kb-clear-btn {
  border: none;
  background: transparent;
  color: var(--color-green);
  cursor: pointer;
  font-size: 12px;
}
.kb-clear-btn:hover {
  text-decoration: underline;
}

.kb-stats {
  padding: 6px 12px 10px;
  font-size: 11px;
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
}
.kb-panel :deep(.el-tree-node__expand-icon) {
  color: var(--color-text-tertiary);
}
.kb-panel :deep(.el-checkbox) {
  margin-right: 2px;
}
</style>
