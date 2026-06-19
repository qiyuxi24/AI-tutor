<script setup>
/**
 * UserProfile.vue — 用户画像面板
 *
 * 功能：查看 / 编辑用户画像（Markdown 格式）
 * 数据流：emit profile-updated → HomeView 刷新图谱上下文
 */
import { ref, watch } from 'vue'
import { marked } from 'marked'
import { getProfile, updateProfile } from '../api/index.js'

const props = defineProps({
  visible: { type: Boolean, default: false },
})

const emit = defineEmits(['close', 'profile-updated'])

/* ================================================================
   状态
   ================================================================ */
const loading = ref(false)
const error = ref('')
const content = ref('')
const editMode = ref(false)
const editContent = ref('')
const saving = ref(false)

/* ================================================================
   Markdown 渲染
   ================================================================ */
function renderMarkdown(md) {
  if (!md) return '<p class="profile-empty-hint">暂无画像，点击编辑开始填写</p>'
  return marked.parse(md)
}

/* ================================================================
   加载画像
   ================================================================ */
async function loadProfile() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await getProfile()
    content.value = data.content
  } catch (e) {
    error.value = e.response?.data?.detail || '加载用户画像失败'
  } finally {
    loading.value = false
  }
}

watch(() => props.visible, (val) => {
  if (val) loadProfile()
})

/* ================================================================
   编辑操作
   ================================================================ */
function enterEdit() {
  editContent.value = content.value
  editMode.value = true
}

function cancelEdit() {
  editMode.value = false
}

async function saveProfile() {
  saving.value = true
  error.value = ''
  try {
    const { data } = await updateProfile(editContent.value)
    content.value = data.content
    editMode.value = false
    emit('profile-updated')
  } catch (e) {
    error.value = e.response?.data?.detail || '保存失败'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="profile-backdrop" @click.self="emit('close')">
      <div class="profile-panel" @click.stop>
        <!-- 头部 -->
        <div class="profile-header">
          <h3 class="profile-title">用户画像</h3>
          <div class="profile-header-actions">
            <button
              v-if="!editMode"
              class="btn btn-edit"
              @click="enterEdit"
            >
              编辑
            </button>
            <button class="btn btn-close" @click="emit('close')">×</button>
          </div>
        </div>

        <!-- 内容区 -->
        <div class="profile-body">
          <!-- 加载中 -->
          <div v-if="loading" class="profile-loading">加载中...</div>

          <!-- 错误 -->
          <div v-else-if="error" class="profile-error">{{ error }}</div>

          <!-- 预览模式 -->
          <div
            v-else-if="!editMode"
            class="profile-preview markdown-body"
            v-html="renderMarkdown(content)"
          ></div>

          <!-- 编辑模式 -->
          <div v-else class="profile-editor-wrap">
            <textarea
              v-model="editContent"
              class="profile-editor"
              placeholder="填写用户画像（Markdown 格式）..."
            ></textarea>
          </div>
        </div>

        <!-- 底部按钮 -->
        <div class="profile-footer">
          <button
            v-if="editMode"
            class="btn btn-cancel"
            @click="cancelEdit"
          >
            取消
          </button>
          <button
            v-if="editMode"
            class="btn btn-save"
            :disabled="saving"
            @click="saveProfile"
          >
            {{ saving ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* ================================================================
   遮罩 + 面板
   ================================================================ */
.profile-backdrop {
  position: fixed;
  inset: 0;
  background: var(--color-bg-overlay);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(2px);
}

.profile-panel {
  background: var(--color-bg-primary);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  box-shadow: var(--shadow-popup);
  width: 640px;
  max-width: 90vw;
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ================================================================
   头部
   ================================================================ */
.profile-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.profile-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.profile-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.btn {
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid transparent;
  transition: background 0.15s, border-color 0.15s;
}

.btn-edit {
  background: transparent;
  color: var(--color-blue);
  border-color: var(--color-border);
}

.btn-edit:hover {
  background: var(--color-bg-surface);
}

.btn-close {
  background: none;
  border: none;
  color: var(--color-text-secondary);
  font-size: 20px;
  padding: 0 4px;
  line-height: 1;
  cursor: pointer;
}

.btn-close:hover {
  color: var(--color-text-primary);
}

/* ================================================================
   内容区
   ================================================================ */
.profile-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.profile-loading,
.profile-error {
  text-align: center;
  padding: 40px 0;
  color: var(--color-text-secondary);
  font-size: 14px;
}

.profile-error {
  color: var(--color-red);
}

.profile-empty-hint {
  color: var(--color-text-muted);
  font-style: italic;
}

/* ── Markdown 预览 ── */
.profile-preview :deep(h1) {
  font-size: 20px;
  font-weight: 600;
  margin-top: 0;
  margin-bottom: 16px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--color-border);
}

.profile-preview :deep(h2) {
  font-size: 15px;
  font-weight: 600;
  margin-top: 20px;
  margin-bottom: 10px;
  color: var(--color-text-primary);
}

.profile-preview :deep(h3) {
  font-size: 13px;
  font-weight: 600;
  margin-top: 16px;
  margin-bottom: 8px;
  color: var(--color-text-secondary);
}

.profile-preview :deep(p) {
  margin: 6px 0;
  line-height: 1.6;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.profile-preview :deep(strong) {
  color: var(--color-text-primary);
}

.profile-preview :deep(ul),
.profile-preview :deep(ol) {
  margin: 6px 0;
  padding-left: 20px;
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.profile-preview :deep(li) {
  margin: 3px 0;
}

.profile-preview :deep(blockquote) {
  margin: 10px 0;
  padding: 8px 14px;
  border-left: 3px solid var(--color-blue);
  background: var(--color-bg-surface);
  border-radius: 0 6px 6px 0;
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.profile-preview :deep(blockquote p) {
  margin: 4px 0;
}

.profile-preview :deep(hr) {
  border: none;
  border-top: 1px solid var(--color-border);
  margin: 16px 0;
}

/* ── 编辑器 ── */
.profile-editor-wrap {
  height: 100%;
}

.profile-editor {
  width: 100%;
  min-height: 400px;
  padding: 14px;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  color: var(--color-text-primary);
  font-size: 13px;
  font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
  line-height: 1.6;
  resize: vertical;
}

.profile-editor:focus {
  outline: none;
  border-color: var(--color-blue);
}

/* ================================================================
   底部按钮
   ================================================================ */
.profile-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 14px 20px;
  border-top: 1px solid var(--color-border);
  flex-shrink: 0;
}

.btn-cancel {
  background: transparent;
  color: var(--color-text-secondary);
  border-color: var(--color-border);
}

.btn-cancel:hover {
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}

.btn-save {
  background: var(--color-blue);
  color: #fff;
  border-color: var(--color-blue);
}

.btn-save:hover:not(:disabled) {
  background: var(--color-blue-hover);
}

.btn-save:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
