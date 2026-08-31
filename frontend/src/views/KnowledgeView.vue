<script setup>
/**
 * KnowledgeView.vue — 知识库页（活动栏第三个入口）
 *
 * 布局：左侧资源管理器式目录树（KbPanel）+ 右侧内容区（统计概览 + 生成学科图谱）
 *
 * 职责：
 *   - 展示知识库文件树，支持建文件夹 / 上传 / 删除 / 选择检索范围
 *   - 右侧展示知识库统计（文件数、分块数）
 *   - 右侧提供「从学科书籍生成知识图谱」功能：
 *     选择学科名 + 勾选书籍来源（文件/文件夹）→ AI 分析书本内容生成图谱
 */

import { ref, onMounted, nextTick } from 'vue'
import { getKbStats, getKbTree, generateKbGraph } from '../api/kb.js'
import KbPanel from '../components/KbPanel.vue'

const stats = ref({ files: 0, chunks: 0 })
const statsLoading = ref(true)
const statsError = ref('')

// 当前选择的上下文（供 KbPanel 上下文提示展示，此处仅记录不联动对话）
const selectedContext = ref(null)

// ─── 生成学科图谱 ───
const treeRef = ref(null)
const tree = ref([])
const subjectInput = ref('')           // 学科名
const genMode = ref('subject')         // subject=整学科 / section=按章节
const generating = ref(false)
const genResult = ref(null)            // 生成结果
const genError = ref('')

const treeProps = { children: 'children', label: 'name' }

async function loadStats() {
  statsLoading.value = true
  statsError.value = ''
  try {
    const res = await getKbStats()
    stats.value = {
      files: res.data?.files ?? 0,
      chunks: res.data?.chunks ?? 0,
    }
  } catch (e) {
    statsError.value = e.response?.data?.detail || '加载统计失败'
  } finally {
    statsLoading.value = false
  }
}

async function loadTree() {
  try {
    const res = await getKbTree()
    tree.value = res.data.tree || []
  } catch (e) {
    // 树加载失败不影响其它功能
  }
}

function handleContextChange(ctx) {
  selectedContext.value = ctx
}

/**
 * 获取勾选的文件/文件夹节点 ID。
 * 勾选父节点时也会收集其下所有文件（生成时后端会自动展开文件夹）。
 */
function getCheckedNodeIds() {
  const checked = treeRef.value?.getCheckedKeys() || []
  const halfChecked = treeRef.value?.getHalfCheckedKeys() || []
  return [...new Set([...checked, ...halfChecked])]
}

async function handleGenerate() {
  const subject = subjectInput.value.trim()
  if (!subject) {
    genError.value = '请输入学科名（如"数据结构"）'
    genResult.value = null
    return
  }
  const nodeIds = getCheckedNodeIds()
  if (!nodeIds.length) {
    genError.value = '请先勾选至少一个文件或文件夹作为书籍来源'
    genResult.value = null
    return
  }

  generating.value = true
  genError.value = ''
  genResult.value = null
  try {
    const res = await generateKbGraph(subject, nodeIds, genMode.value)
    const d = res.data || {}
    genResult.value = {
      subject,
      createdNodes: (d.created_nodes || []).length,
      createdEdges: d.created_edges || 0,
      skippedNodes: (d.skipped_nodes || []).length,
      nodeNames: d.created_nodes || [],
    }
  } catch (e) {
    genError.value = e.response?.data?.detail || e.message || '生成失败'
  } finally {
    generating.value = false
  }
}

onMounted(() => {
  loadStats()
  loadTree()
  nextTick(() => {
    // 确保目录树渲染后可以展开全部，便于勾选
  })
})
</script>

<template>
  <div class="knowledge-layout">
    <!-- 左侧：资源管理器式目录树（作为知识库页的二级侧栏） -->
    <div class="kb-sidebar">
      <KbPanel @context-change="handleContextChange" />
    </div>

    <!-- 右侧：知识库概览 + 生成学科图谱 -->
    <main class="kb-content">
      <div class="kb-content-inner">
        <header class="kc-header">
          <h2>知识库</h2>
          <p class="kc-subtitle">上传教材 / PDF / Word 等文件，AI 自动解析向量化，按目录管理并可在对话中检索。</p>
        </header>

        <!-- 统计卡片 -->
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-icon">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
              </svg>
            </div>
            <div class="stat-info">
              <div class="stat-value" :class="{ muted: statsLoading }">
                {{ statsLoading ? '—' : stats.files }}
              </div>
              <div class="stat-label">文件数</div>
            </div>
          </div>

          <div class="stat-card">
            <div class="stat-icon">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
              </svg>
            </div>
            <div class="stat-info">
              <div class="stat-value" :class="{ muted: statsLoading }">
                {{ statsLoading ? '—' : stats.chunks }}
              </div>
              <div class="stat-label">向量分块</div>
            </div>
          </div>

          <div class="stat-card">
            <div class="stat-icon">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
              </svg>
            </div>
            <div class="stat-info">
              <div class="stat-value muted">PDF/DOCX</div>
              <div class="stat-label">格式支持</div>
            </div>
          </div>
        </div>

        <p v-if="statsError" class="stats-error">{{ statsError }}</p>

        <!-- 从书籍生成学科图谱 -->
        <section class="gen-section">
          <h3>从学科书籍生成知识图谱</h3>
          <p class="gen-subtitle">
            勾选左侧目录中的书籍文件，输入学科名，AI 会分析书本内容，自动生成该学科的知识图谱（节点 + 知识点关系）。
          </p>

          <div class="gen-row">
            <el-input
              v-model="subjectInput"
              placeholder="学科名，如：数据结构"
              maxlength="100"
              class="gen-subject-input"
            />
            <el-select v-model="genMode" class="gen-mode-select">
              <el-option label="整学科一键生成" value="subject" />
              <el-option label="按章节增量生成" value="section" />
            </el-select>
            <el-button
              type="primary"
              :loading="generating"
              :disabled="!tree.length"
              @click="handleGenerate"
            >
              {{ generating ? 'AI 生成中...' : '生成学科图谱' }}
            </el-button>
          </div>

          <div class="gen-tree-wrap" v-if="tree.length">
            <div class="gen-tree-label">选择书籍来源（可勾选文件夹，将包含其下所有文件）</div>
            <el-tree
              ref="treeRef"
              :data="tree"
              node-key="id"
              :props="treeProps"
              show-checkbox
              default-expand-all
              class="gen-tree"
            />
          </div>
          <div v-else class="gen-empty">暂无书籍，请先在左侧上传教材 / PDF / Word 等文件。</div>

          <div v-if="genError" class="gen-error">{{ genError }}</div>

          <div v-if="genResult" class="gen-result">
            <div class="gen-result-title">生成完成「{{ genResult.subject }}」</div>
            <div class="gen-result-stats">
              <div class="gen-stat">
                <div class="gen-stat-value">{{ genResult.createdNodes }}</div>
                <div class="gen-stat-label">新增知识点</div>
              </div>
              <div class="gen-stat">
                <div class="gen-stat-value">{{ genResult.createdEdges }}</div>
                <div class="gen-stat-label">新增关系</div>
              </div>
              <div class="gen-stat">
                <div class="gen-stat-value">{{ genResult.skippedNodes }}</div>
                <div class="gen-stat-label">已存在跳过</div>
              </div>
            </div>
            <div class="gen-result-tip">已自动写入知识图谱，可切换到「图谱」页查看该学科的知识点关系图。</div>
          </div>
        </section>

        <!-- 使用说明 -->
        <section class="usage-section">
          <h3>使用说明</h3>
          <ul class="usage-list">
            <li>
              <span class="usage-icon">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
              </span>
              点击左侧「＋」新建文件夹，将学习资料按科目 / 章节分类整理。
            </li>
            <li>
              <span class="usage-icon">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
              </span>
              支持上传 <strong>PDF / Word / Markdown / TXT</strong>，自动解析并向量化，无需手动处理。
            </li>
            <li>
              <span class="usage-icon">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
              </span>
              勾选书籍文件后点击「生成学科图谱」，AI 从书本内容提取知识点并建立关系。
            </li>
            <li>
              <span class="usage-icon">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
              </span>
              切换到「图谱」页，可查看 / 学习该学科的知识图谱。
            </li>
          </ul>
        </section>

        <!-- 当前上下文提示 -->
        <div v-if="selectedContext" class="kb-context-bar">
          当前检索范围：<strong>{{ selectedContext.nodeName }}</strong>
          <span v-if="selectedContext.fileCount">（{{ selectedContext.fileCount }} 个文件）</span>
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped>
.knowledge-layout {
  display: flex;
  width: 100%;
  height: 100%;
}

/* 左侧目录树侧栏 */
.kb-sidebar {
  width: 280px;
  min-width: 280px;
  height: 100%;
  border-right: 1px solid var(--color-border);
  padding: 8px;
  overflow: hidden;
  flex-shrink: 0;
}

/* 右侧内容区 */
.kb-content {
  flex: 1;
  overflow-y: auto;
  padding: 32px 40px;
  background: var(--color-bg-primary);
}

.kb-content::-webkit-scrollbar { width: 5px; }
.kb-content::-webkit-scrollbar-thumb {
  background: var(--color-border-light);
  border-radius: 3px;
}

.kb-content-inner {
  max-width: 780px;
}

.kc-header {
  margin-bottom: 28px;
}

.kc-header h2 {
  font-size: 22px;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0 0 8px;
}

.kc-subtitle {
  font-size: 14px;
  line-height: 1.6;
  color: var(--color-text-secondary);
  margin: 0;
}

/* 统计卡片 */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 28px;
}

.stat-card {
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 18px;
  display: flex;
  align-items: center;
  gap: 14px;
  transition: border-color 0.2s;
}

.stat-card:hover {
  border-color: var(--color-accent);
}

.stat-icon {
  width: 44px;
  height: 44px;
  border-radius: 10px;
  background: var(--color-accent-light);
  color: var(--color-accent);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.stat-value {
  font-size: 24px;
  font-weight: 700;
  color: var(--color-text-primary);
  line-height: 1.2;
}

.stat-value.muted {
  color: var(--color-text-tertiary);
}

.stat-label {
  font-size: 12px;
  color: var(--color-text-tertiary);
  margin-top: 2px;
}

.stats-error {
  color: var(--color-red);
  font-size: 13px;
  margin: -12px 0 20px;
}

/* 生成学科图谱 */
.gen-section {
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 20px 24px;
  margin-bottom: 24px;
}

.gen-section h3 {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 6px;
}

.gen-subtitle {
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-secondary);
  margin: 0 0 16px;
}

.gen-row {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.gen-subject-input {
  width: 180px;
}

.gen-mode-select {
  width: 170px;
}

.gen-tree-wrap {
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  padding: 12px;
  max-height: 260px;
  overflow-y: auto;
}

.gen-tree-label {
  font-size: 12px;
  color: var(--color-text-tertiary);
  margin-bottom: 8px;
}

.gen-tree {
  background: transparent;
}

.gen-empty {
  font-size: 13px;
  color: var(--color-text-tertiary);
  padding: 12px 0;
}

.gen-error {
  margin-top: 12px;
  padding: 10px 14px;
  background: var(--color-red-light, rgba(220, 38, 38, 0.1));
  border: 1px solid var(--color-red);
  border-radius: 8px;
  color: var(--color-red);
  font-size: 13px;
}

.gen-result {
  margin-top: 16px;
  padding: 14px 16px;
  background: var(--color-green-light);
  border: 1px solid var(--color-green);
  border-radius: 10px;
}

.gen-result-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-green);
  margin-bottom: 12px;
}

.gen-result-stats {
  display: flex;
  gap: 32px;
}

.gen-stat-value {
  font-size: 20px;
  font-weight: 700;
  color: var(--color-text-primary);
}

.gen-stat-label {
  font-size: 12px;
  color: var(--color-text-tertiary);
  margin-top: 2px;
}

.gen-result-tip {
  margin-top: 12px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

/* 使用说明 */
.usage-section {
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 20px 24px;
}

.usage-section h3 {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 14px;
}

.usage-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.usage-list li {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-secondary);
}

.usage-list li strong {
  color: var(--color-text-primary);
}

.usage-icon {
  flex-shrink: 0;
  margin-top: 3px;
  color: var(--color-green);
}

/* 当前上下文 */
.kb-context-bar {
  margin-top: 20px;
  padding: 12px 16px;
  background: var(--color-green-light);
  border: 1px solid var(--color-green);
  border-radius: 10px;
  font-size: 13px;
  color: var(--color-green);
}

/* Element Plus 深色适配 */
.gen-section :deep(.el-tree) {
  background: transparent;
  --el-tree-node-hover-bg-color: var(--color-bg-hover);
  --el-tree-text-color: var(--color-text-primary);
}
.gen-section :deep(.el-tree-node__content) {
  height: 30px;
}
</style>
