<script setup>
import { ref, computed, watch } from 'vue'
import { marked } from 'marked'
import { apiClient } from '../api/index.js'

const props = defineProps({
  nodeInfo: { type: Object, default: null },
  visible: { type: Boolean, default: false },
})

const emit = defineEmits(['close', 'refresh'])

const mode = ref('view')        // 'view' | 'edit'
const editContent = ref('')
const saving = ref(false)
const saveError = ref('')

const htmlContent = computed(() => {
  if (!props.nodeInfo?.content) return ''
  return marked(props.nodeInfo.content, { breaks: true })
})

watch(() => props.nodeInfo, (val) => {
  if (val) {
    mode.value = 'view'
    editContent.value = val.content || ''
    saveError.value = ''
  }
})

function displayName(id) {
  if (!id) return ''
  return id.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

async function handleSave() {
  saving.value = true
  saveError.value = ''
  try {
    await apiClient.put(`/api/v1/knowledge/node/${props.nodeInfo.id}`, {
      content: editContent.value,
    })
    emit('refresh')
    mode.value = 'view'
  } catch (e) {
    saveError.value = '保存失败'
  } finally {
    saving.value = false
  }
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
</script>

<template>
  <Transition name="modal">
    <div v-if="visible && nodeInfo" class="modal-overlay" @click.self="emit('close')">
      <div class="modal-panel">
        <!-- 顶部栏 -->
        <div class="modal-header">
          <h2 class="modal-title">{{ nodeInfo.name }}</h2>
          <div class="modal-actions">
            <!-- 阅读/编辑切换 -->
            <button
              v-if="mode === 'view'"
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

        <!-- 掌握程度条 -->
        <div class="mastery-section">
          <div class="mastery-label">
            <span>掌握程度</span>
            <span class="mastery-value">{{ masteryLabel(nodeInfo.mastery) }}</span>
          </div>
          <div class="mastery-bar-bg">
            <div class="mastery-bar-fill" :style="{ width: masteryBarWidth(nodeInfo.mastery) }"></div>
          </div>
        </div>

        <!-- 标签 -->
        <div v-if="nodeInfo.tags?.length" class="tags-row">
          <span v-for="tag in nodeInfo.tags" :key="tag" class="tag">{{ tag }}</span>
          <span v-if="nodeInfo.difficulty" class="tag diff-tag">难度 {{ nodeInfo.difficulty }}/5</span>
          <span v-if="nodeInfo.estimated_minutes" class="tag time-tag">约 {{ nodeInfo.estimated_minutes }} 分钟</span>
        </div>

        <!-- 编辑模式：textarea -->
        <div v-if="mode === 'edit'" class="edit-area">
          <textarea v-model="editContent" class="edit-textarea"></textarea>
          <p v-if="saveError" class="save-error">{{ saveError }}</p>
        </div>

        <!-- 阅读模式：Markdown 渲染 -->
        <div v-else-if="mode === 'view'" class="markdown-body" v-html="htmlContent"></div>

        <!-- 空内容 -->
        <p v-if="!nodeInfo.content && mode === 'view'" class="empty-content">暂无详细内容</p>

        <!-- 前置知识 -->
        <div v-if="nodeInfo.prerequisites?.length" class="relations-section">
          <h3>前置知识</h3>
          <div class="relation-list">
            <span v-for="pid in nodeInfo.prerequisites" :key="pid" class="relation-tag">
              ← {{ displayName(pid) }}
            </span>
          </div>
        </div>

        <!-- 相关节点 -->
        <div v-if="nodeInfo.related_nodes?.length" class="relations-section">
          <h3>相关节点</h3>
          <div class="relation-list">
            <span v-for="rid in nodeInfo.related_nodes" :key="rid" class="relation-tag related">
              ↔ {{ displayName(rid) }}
            </span>
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
  width: min(680px, 90vw); max-height: 85vh;
  background: #ffffff; border-radius: 14px;
  display: flex; flex-direction: column;
  box-shadow: 0 20px 60px rgba(0,0,0,0.3);
  overflow: hidden;
}

.modal-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 18px 24px; border-bottom: 1px solid #f1f5f9;
  flex-shrink: 0;
}

.modal-title { font-size: 18px; font-weight: 700; color: #0f172a; margin: 0; }
.modal-actions { display: flex; gap: 8px; }

.action-btn {
  display: flex; align-items: center; gap: 5px;
  padding: 6px 14px; border: 1px solid #e2e8f0;
  border-radius: 8px; background: #fff; color: #475569;
  font-size: 13px; cursor: pointer; transition: all 0.15s;
  white-space: nowrap;
}
.action-btn:hover { background: #f8fafc; border-color: #cbd5e1; }
.edit-btn { color: #4f46e5; border-color: #c7d2fe; }
.edit-btn:hover { background: #eef2ff; }
.save-btn { color: #059669; border-color: #a7f3d0; }
.save-btn:hover { background: #ecfdf5; }
.save-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.cancel-btn { color: #64748b; }
.close-btn { border: none; background: transparent; color: #94a3b8; padding: 6px; }
.close-btn:hover { background: #f1f5f9; color: #475569; }

/* 掌握程度条 */
.mastery-section { padding: 12px 24px; background: #f8fafc; flex-shrink: 0; }
.mastery-label { display: flex; justify-content: space-between; font-size: 12px; color: #64748b; margin-bottom: 5px; }
.mastery-value { color: #059669; font-weight: 600; }
.mastery-bar-bg { height: 6px; background: #e2e8f0; border-radius: 3px; overflow: hidden; }
.mastery-bar-fill { height: 100%; background: linear-gradient(90deg, #86efac, #059669); border-radius: 3px; transition: width 0.3s ease; }

.tags-row { display: flex; flex-wrap: wrap; gap: 6px; padding: 12px 24px; flex-shrink: 0; }
.tag { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: 500; background: #eef2ff; color: #4f46e5; }
.diff-tag { background: #fef3c7; color: #d97706; }
.time-tag { background: #f0fdf4; color: #16a34a; }

/* 编辑区 */
.edit-area { flex: 1; overflow-y: auto; padding: 16px 24px; }
.edit-textarea {
  width: 100%; min-height: 300px; padding: 14px;
  border: 1px solid #e2e8f0; border-radius: 10px;
  font-family: 'Consolas', 'Courier New', monospace;
  font-size: 14px; line-height: 1.6; resize: vertical;
  color: #1e293b; background: #f8fafc;
}
.edit-textarea:focus { outline: none; border-color: #4f46e5; }
.save-error { color: #ef4444; font-size: 13px; margin-top: 8px; }

/* Markdown */
.markdown-body {
  flex: 1; overflow-y: auto; padding: 20px 24px;
  font-size: 14px; line-height: 1.7; color: #1e293b;
}
.markdown-body :deep(p) { margin: 0 0 10px; }
.markdown-body :deep(strong) { font-weight: 600; }
.markdown-body :deep(code) { background: #f1f5f9; padding: 2px 6px; border-radius: 4px; font-family: 'Consolas', monospace; font-size: 13px; }
.markdown-body :deep(pre) { background: #1e293b; color: #e2e8f0; padding: 14px 16px; border-radius: 10px; overflow-x: auto; margin: 12px 0; font-size: 13px; }
.markdown-body :deep(pre code) { background: transparent; padding: 0; color: inherit; }
.markdown-body :deep(h1), .markdown-body :deep(h2), .markdown-body :deep(h3) { margin: 18px 0 8px; font-weight: 600; color: #0f172a; }
.markdown-body :deep(h1) { font-size: 20px; }
.markdown-body :deep(h2) { font-size: 17px; }
.markdown-body :deep(blockquote) { border-left: 3px solid #4f46e5; padding-left: 14px; margin: 12px 0; color: #64748b; }
.markdown-body :deep(ul), .markdown-body :deep(ol) { padding-left: 20px; margin: 8px 0; }
.empty-content { color: #94a3b8; font-style: italic; text-align: center; padding: 40px 0; }

/* 关系 */
.relations-section { padding: 14px 24px 18px; border-top: 1px solid #f1f5f9; flex-shrink: 0; }
.relations-section h3 { font-size: 13px; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; margin: 0 0 8px; }
.relation-list { display: flex; flex-wrap: wrap; gap: 6px; }
.relation-tag { padding: 3px 10px; border-radius: 6px; font-size: 12px; background: #eff6ff; color: #3b82f6; border: 1px solid #dbeafe; }
.relation-tag.related { background: #f5f3ff; color: #8b5cf6; border-color: #e9d5ff; }

/* 弹窗动画 */
.modal-enter-active, .modal-leave-active { transition: opacity 0.2s ease; }
.modal-enter-from, .modal-leave-to { opacity: 0; }
.modal-enter-from .modal-panel, .modal-leave-to .modal-panel { transform: scale(0.95); }
</style>
