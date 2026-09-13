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
            <input type="file" hidden multiple :accept="acceptTypes" @change="handleUpload">
          </label>
        </el-tooltip>
      </div>
    </div>

    <!-- 目录树 -->
    <div class="kb-tree-wrap" v-if="tree.length">
      <el-tree
        ref="treeRef"
        :data="tree"
        node-key="id"
        :props="treeProps"
        :default-expand-all="false"
        :expand-on-click-node="false"
        highlight-current
        @node-click="handleNodeClick"
      >
        <template #default="{ data }">
          <div class="kb-node" :class="{ selected: selectedNode?.id === data.id }">
            <span class="kb-node-icon">
              <svg v-if="data.type === 'folder'" viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>
              <svg v-else viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z"/></svg>
            </span>
            <span class="kb-node-name">{{ data.name }}</span>
            <span class="kb-node-actions">
              <button class="kb-mini-btn" title="放入上下文" @click.stop="setContext(data)">
                <svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M13 7h-2v4H7v2h4v4h2v-4h4v-2h-4V7zm-1-5C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2z"/></svg>
              </button>
              <button class="kb-mini-btn" title="删除" @click.stop="confirmDelete(data)">
                <svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
              </button>
            </span>
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
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getKbTree,
  createKbFolder,
  uploadKbFile,
  deleteKbNode,
  getKbContext,
} from '../api/kb.js'

const treeRef = ref(null)
const tree = ref([])
const selectedNode = ref(null)
const showCreateDialog = ref(false)
const newFolderName = ref('')
const creating = ref(false)
const uploading = ref(false)

// 当前放入上下文的范围（单个文件或文件夹）
const contextNode = ref(null)
const contextLabel = ref('')

const treeProps = {
  children: 'children',
  label: 'name',
}

// 与后端 parsers 包支持的格式保持一致
const acceptTypes = '.pdf,.docx,.pptx,.md,.markdown,.txt,.png,.jpg,.jpeg,.bmp,.webp,.tiff,.gif,.csv,.json,.log,.py,.js,.ts,.html,.xml,.epub,.fb2,.mobi,.azw,.azw3,.djvu'

const emit = defineEmits(['context-change'])

async function loadTree() {
  try {
    const res = await getKbTree()
    tree.value = res.data.tree || []
  } catch (e) {
    ElMessage.error('加载知识库失败：' + (e.response?.data?.detail || e.message))
  }
}

function handleNodeClick(data) {
  selectedNode.value = data
}

function openCreateFolder() {
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

async function handleUpload(event) {
  const files = Array.from(event.target.files || [])
  if (!files.length) return
  const parentId = selectedNode.value?.type === 'folder' ? selectedNode.value.id : null

  uploading.value = true
  ElMessage.info(`正在上传 ${files.length} 个文件，解析并向量化中...`)
  try {
    for (const file of files) {
      await uploadKbFile(file, parentId)
    }
    ElMessage.success(`成功上传 ${files.length} 个文件`)
    loadTree()
  } catch (e) {
    ElMessage.error('上传失败：' + (e.response?.data?.detail || e.message))
  } finally {
    uploading.value = false
    event.target.value = ''
  }
}

async function setContext(data) {
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
    emit('context-change', {
      nodeId: data.id,
      nodeName: data.name,
      fileCount,
      nodeIds: res.data.node_ids || [],   // 实际检索的文件节点 ID 列表
    })
    ElMessage.success(`已设置检索范围：${contextLabel.value}`)
  } catch (e) {
    ElMessage.error('设置失败：' + (e.response?.data?.detail || e.message))
  }
}

function clearContext() {
  contextNode.value = null
  contextLabel.value = ''
  emit('context-change', null)
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
  } catch (e) {
    ElMessage.error('删除失败：' + (e.response?.data?.detail || e.message))
  }
}

onMounted(loadTree)
</script>

<style scoped>
.kb-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--color-bg-primary);
  border-radius: 10px;
  overflow: hidden;
}

.kb-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid var(--color-border);
}

.kb-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.kb-actions {
  display: flex;
  gap: 6px;
}

.kb-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
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

.kb-tree-wrap {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
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

.kb-node-actions {
  display: none;
  gap: 4px;
}
.kb-node:hover .kb-node-actions {
  display: flex;
}

.kb-mini-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: pointer;
}
.kb-mini-btn:hover {
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}

.kb-empty {
  flex: 1;
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
  padding: 10px 14px;
  border-top: 1px solid var(--color-border);
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
</style>
