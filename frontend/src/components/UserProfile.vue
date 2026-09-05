<script setup>
/**
 * UserProfile.vue — 用户画像面板（结构化 v2）
 *
 * 功能：
 *  - 概览：结构化卡片展示（基本信息 / 学习状态 / 性格偏好 / AI 观察时间线）
 *  - 编辑：表单为主（保存走 PATCH 结构化），Markdown 源码编辑保留为高级模式
 *  - 完整度：进度条展示画像完善程度
 * 数据流：emit profile-updated → HomeView 刷新图谱上下文
 */
import { ref, watch, computed } from 'vue'
import { renderMarkdown as renderMd } from '../utils/markdown.js'
import {
  getProfile,
  updateProfile,
  saveProfileData,
  deleteProfileNote,
} from '../api/index.js'

const props = defineProps({
  visible: { type: Boolean, default: false },
})

const emit = defineEmits(['close', 'profile-updated'])

/* ================================================================
   状态
   ================================================================ */
const loading = ref(false)
const error = ref('')
const saving = ref(false)

// 服务端返回的完整数据
const profileContent = ref('')       // 渲染后的 Markdown
const profileData = ref(null)        // 结构化数据
const completeness = ref({ percent: 0, filled: 0, total: 0, fields: {} })

// 视图状态
const viewTab = ref('overview')      // overview | markdown
const editMode = ref(false)
const editTab = ref('form')          // form | source
const form = ref(null)               // 表单编辑副本
const sourceContent = ref('')        // Markdown 源码编辑副本

/* ================================================================
   派生
   ================================================================ */
const percent = computed(() => completeness.value.percent || 0)
const notes = computed(() => profileData.value?.ai_notes || [])

function fmtTime(iso) {
  if (!iso) return ''
  return iso.slice(0, 16).replace('T', ' ')
}

/* ================================================================
   加载画像
   ================================================================ */
function defaultForm(data) {
  const d = data || {}
  const basic = d.basic || {}
  const learning = d.learning || {}
  const prefs = d.preferences || {}
  return {
    basic: { name: basic.name || '', age: basic.age || '', stage: basic.stage || '' },
    goals: (d.goals || []).join('\n'),
    knowledge_background: d.knowledge_background || '',
    learning: { pace: learning.pace || '', weekly_hours: learning.weekly_hours || '' },
    preferences: {
      personality: prefs.personality || '',
      teaching_style_like: prefs.teaching_style_like || '',
      teaching_style_avoid: prefs.teaching_style_avoid || '',
    },
  }
}

function applyProfile(res) {
  profileContent.value = res.content || ''
  profileData.value = res.data || null
  completeness.value = res.completeness || { percent: 0, filled: 0, total: 0, fields: {} }
  viewTab.value = 'overview'
}

async function loadProfile() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await getProfile()
    applyProfile(data)
  } catch (e) {
    error.value = e.response?.data?.detail || '加载用户画像失败'
  } finally {
    loading.value = false
  }
}

watch(() => props.visible, (val) => {
  if (val) {
    editMode.value = false
    loadProfile()
  }
})

/* ================================================================
   Markdown 渲染（高级模式预览）
   ================================================================ */
function renderMarkdown(md) {
  if (!md) return '<p class="profile-empty-hint">暂无画像，点击编辑开始填写</p>'
  return renderMd(md)
}

/* ================================================================
   编辑操作
   ================================================================ */
function enterEdit() {
  form.value = defaultForm(profileData.value)
  sourceContent.value = profileContent.value
  editTab.value = 'form'
  editMode.value = true
}

function cancelEdit() {
  editMode.value = false
  error.value = ''
}

function applySaved(res) {
  applyProfile(res)
  editMode.value = false
  emit('profile-updated')
}

/** 表单保存：结构化 PATCH */
async function saveForm() {
  saving.value = true
  error.value = ''
  try {
    const f = form.value
    const data = {
      basic: f.basic,
      goals: f.goals.split('\n').map((s) => s.trim()).filter(Boolean),
      knowledge_background: f.knowledge_background,
      learning: f.learning,
      preferences: f.preferences,
    }
    const { data: res } = await saveProfileData(data)
    applySaved(res)
  } catch (e) {
    error.value = e.response?.data?.detail || '保存失败'
  } finally {
    saving.value = false
  }
}

/** 源码保存：Markdown 高级模式（全量替换） */
async function saveSource() {
  saving.value = true
  error.value = ''
  try {
    const { data: res } = await updateProfile(sourceContent.value, 'replace')
    applySaved(res)
  } catch (e) {
    error.value = e.response?.data?.detail || '保存失败'
  } finally {
    saving.value = false
  }
}

function saveCurrent() {
  return editTab.value === 'form' ? saveForm() : saveSource()
}

/** 删除观察笔记 */
async function removeNote(noteId) {
  if (!confirm('确定删除这条 AI 观察笔记吗？')) return
  saving.value = true
  error.value = ''
  try {
    const { data: res } = await deleteProfileNote(noteId)
    applyProfile(res)
    emit('profile-updated')
  } catch (e) {
    error.value = e.response?.data?.detail || '删除失败'
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
          <div class="profile-title-wrap">
            <h3 class="profile-title">用户画像</h3>
            <span class="profile-completeness" :title="`已填写 ${completeness.filled}/${completeness.total} 项`">
              <i
                class="completeness-bar"
                :style="{ width: percent + '%' }"
              ></i>
              <em>{{ percent }}%</em>
            </span>
          </div>
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

          <!-- ═══ 查看模式 ═══ -->
          <template v-else-if="!editMode">
            <div class="profile-tabs">
              <button
                class="profile-tab"
                :class="{ active: viewTab === 'overview' }"
                @click="viewTab = 'overview'"
              >概览</button>
              <button
                class="profile-tab"
                :class="{ active: viewTab === 'markdown' }"
                @click="viewTab = 'markdown'"
              >Markdown</button>
            </div>

            <!-- 概览视图 -->
            <div v-if="viewTab === 'overview'" class="profile-overview">
              <!-- 基本信息 -->
              <section class="pf-section">
                <h4 class="pf-section-title">基本信息</h4>
                <div class="pf-grid">
                  <div class="pf-field">
                    <span class="pf-label">姓名/昵称</span>
                    <span class="pf-value" :class="{ empty: !profileData?.basic?.name }">
                      {{ profileData?.basic?.name || '未填写' }}
                    </span>
                  </div>
                  <div class="pf-field">
                    <span class="pf-label">年龄</span>
                    <span class="pf-value" :class="{ empty: !profileData?.basic?.age }">
                      {{ profileData?.basic?.age || '未填写' }}
                    </span>
                  </div>
                  <div class="pf-field">
                    <span class="pf-label">年级/阶段</span>
                    <span class="pf-value" :class="{ empty: !profileData?.basic?.stage }">
                      {{ profileData?.basic?.stage || '未填写' }}
                    </span>
                  </div>
                </div>
              </section>

              <!-- 学习状态 -->
              <section class="pf-section">
                <h4 class="pf-section-title">学习状态</h4>
                <div class="pf-grid">
                  <div class="pf-field pf-field-full">
                    <span class="pf-label">当前学习目标</span>
                    <span class="pf-value" :class="{ empty: !profileData?.goals?.length }">
                      <template v-if="profileData?.goals?.length">
                        <span v-for="g in profileData.goals" :key="g" class="pf-goal-chip">{{ g }}</span>
                      </template>
                      <template v-else>未填写</template>
                    </span>
                  </div>
                  <div class="pf-field pf-field-full">
                    <span class="pf-label">知识背景</span>
                    <span class="pf-value" :class="{ empty: !profileData?.knowledge_background }">
                      {{ profileData?.knowledge_background || '未填写' }}
                    </span>
                  </div>
                  <div class="pf-field">
                    <span class="pf-label">学习节奏偏好</span>
                    <span class="pf-value" :class="{ empty: !profileData?.learning?.pace }">
                      {{ profileData?.learning?.pace || '未填写' }}
                    </span>
                  </div>
                  <div class="pf-field">
                    <span class="pf-label">每周学习时间</span>
                    <span class="pf-value" :class="{ empty: !profileData?.learning?.weekly_hours }">
                      {{ profileData?.learning?.weekly_hours || '未填写' }}
                    </span>
                  </div>
                </div>
              </section>

              <!-- 性格与偏好 -->
              <section class="pf-section">
                <h4 class="pf-section-title">性格与偏好</h4>
                <div class="pf-grid">
                  <div class="pf-field">
                    <span class="pf-label">性格特点</span>
                    <span class="pf-value" :class="{ empty: !profileData?.preferences?.personality }">
                      {{ profileData?.preferences?.personality || '未填写' }}
                    </span>
                  </div>
                  <div class="pf-field">
                    <span class="pf-label">喜欢的教学方式</span>
                    <span class="pf-value" :class="{ empty: !profileData?.preferences?.teaching_style_like }">
                      {{ profileData?.preferences?.teaching_style_like || '未填写' }}
                    </span>
                  </div>
                  <div class="pf-field pf-field-full">
                    <span class="pf-label">需要避免的方式</span>
                    <span class="pf-value" :class="{ empty: !profileData?.preferences?.teaching_style_avoid }">
                      {{ profileData?.preferences?.teaching_style_avoid || '未填写' }}
                    </span>
                  </div>
                </div>
              </section>

              <!-- AI 观察时间线 -->
              <section class="pf-section">
                <h4 class="pf-section-title">
                  AI 观察记录
                  <span class="pf-section-sub" v-if="notes.length">共 {{ notes.length }} 条</span>
                </h4>
                <div v-if="notes.length" class="pf-notes">
                  <div v-for="note in notes" :key="note.id" class="pf-note">
                    <div class="pf-note-head">
                      <span class="pf-note-time">{{ fmtTime(note.created_at) }}</span>
                      <button class="pf-note-del" title="删除" @click="removeNote(note.id)">×</button>
                    </div>
                    <div class="pf-note-body">{{ note.content }}</div>
                  </div>
                </div>
                <p v-else class="pf-notes-empty">
                  AI 在教学过程中会自动记录观察笔记，这里会显示学生的薄弱点、学习风格等个性化信息。
                </p>
              </section>
            </div>

            <!-- Markdown 视图 -->
            <div
              v-else
              class="profile-preview markdown-body"
              v-html="renderMarkdown(profileContent)"
            ></div>
          </template>

          <!-- ═══ 编辑模式 ═══ -->
          <template v-else>
            <div class="profile-tabs">
              <button
                class="profile-tab"
                :class="{ active: editTab === 'form' }"
                @click="editTab = 'form'"
              >表单编辑</button>
              <button
                class="profile-tab"
                :class="{ active: editTab === 'source' }"
                @click="editTab = 'source'"
              >Markdown 源码</button>
            </div>

            <!-- 表单编辑 -->
            <div v-if="editTab === 'form'" class="profile-form">
              <h4 class="pf-section-title">基本信息</h4>
              <div class="pf-grid">
                <label class="pf-input-wrap">
                  <span class="pf-input-label">姓名/昵称</span>
                  <input v-model="form.basic.name" class="pf-input" placeholder="如：小明" />
                </label>
                <label class="pf-input-wrap">
                  <span class="pf-input-label">年龄</span>
                  <input v-model="form.basic.age" class="pf-input" placeholder="如：20" />
                </label>
                <label class="pf-input-wrap">
                  <span class="pf-input-label">年级/阶段</span>
                  <input v-model="form.basic.stage" class="pf-input" placeholder="如：大二、高二、工作中" />
                </label>
              </div>

              <h4 class="pf-section-title">学习状态</h4>
              <div class="pf-grid">
                <label class="pf-input-wrap pf-field-full">
                  <span class="pf-input-label">当前学习目标（每行一个）</span>
                  <textarea v-model="form.goals" class="pf-input pf-textarea" rows="3" placeholder="如：&#10;学习 Python 编程&#10;备战六级英语"></textarea>
                </label>
                <label class="pf-input-wrap pf-field-full">
                  <span class="pf-input-label">知识背景</span>
                  <textarea v-model="form.knowledge_background" class="pf-input pf-textarea" rows="2" placeholder="如：已掌握初中数学、有 JS 基础"></textarea>
                </label>
                <label class="pf-input-wrap">
                  <span class="pf-input-label">学习节奏偏好</span>
                  <input v-model="form.learning.pace" class="pf-input" placeholder="如：喜欢慢节奏深入理解" />
                </label>
                <label class="pf-input-wrap">
                  <span class="pf-input-label">每周学习时间</span>
                  <input v-model="form.learning.weekly_hours" class="pf-input" placeholder="如：每天 1 小时" />
                </label>
              </div>

              <h4 class="pf-section-title">性格与偏好</h4>
              <div class="pf-grid">
                <label class="pf-input-wrap">
                  <span class="pf-input-label">性格特点</span>
                  <input v-model="form.preferences.personality" class="pf-input" placeholder="如：内向谨慎、容易焦虑" />
                </label>
                <label class="pf-input-wrap">
                  <span class="pf-input-label">喜欢的教学方式</span>
                  <input v-model="form.preferences.teaching_style_like" class="pf-input" placeholder="如：喜欢苏格拉底式提问" />
                </label>
                <label class="pf-input-wrap pf-field-full">
                  <span class="pf-input-label">需要避免的方式</span>
                  <input v-model="form.preferences.teaching_style_avoid" class="pf-input" placeholder="如：不喜欢被催促、不喜欢太抽象的讲解" />
                </label>
              </div>
            </div>

            <!-- Markdown 源码编辑（高级模式） -->
            <div v-else class="profile-editor-wrap">
              <textarea
                v-model="sourceContent"
                class="profile-editor"
                placeholder="填写用户画像（Markdown 格式）..."
              ></textarea>
            </div>
          </template>
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
            @click="saveCurrent"
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
  width: 680px;
  max-width: 92vw;
  max-height: 86vh;
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
  padding: 14px 20px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.profile-title-wrap {
  display: flex;
  align-items: center;
  gap: 12px;
}

.profile-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text-primary);
}

/* 完整度进度条 */
.profile-completeness {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 110px;
  height: 8px;
  background: var(--color-bg-secondary);
  border-radius: 4px;
  overflow: hidden;
}

.completeness-bar {
  height: 100%;
  background: linear-gradient(90deg, var(--color-blue), var(--color-blue-hover, var(--color-blue)));
  border-radius: 4px;
  transition: width 0.3s ease;
}

.profile-completeness em {
  font-style: normal;
  font-size: 11px;
  color: var(--color-text-secondary);
  white-space: nowrap;
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
   Tab 切换
   ================================================================ */
.profile-tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 14px;
  border-bottom: 1px solid var(--color-border);
}

.profile-tab {
  background: none;
  border: none;
  padding: 8px 14px;
  font-size: 13px;
  color: var(--color-text-secondary);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: color 0.15s;
}

.profile-tab:hover {
  color: var(--color-text-primary);
}

.profile-tab.active {
  color: var(--color-blue);
  border-bottom-color: var(--color-blue);
}

/* ================================================================
   内容区
   ================================================================ */
.profile-body {
  flex: 1;
  overflow-y: auto;
  padding: 18px 20px;
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

/* ── 概览视图 ── */
.profile-overview {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.pf-section-title {
  margin: 0 0 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-secondary);
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.pf-section-sub {
  font-size: 11px;
  font-weight: 400;
  color: var(--color-text-muted);
}

.pf-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px 14px;
}

.pf-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.pf-field-full {
  grid-column: 1 / -1;
}

.pf-label {
  font-size: 11px;
  color: var(--color-text-muted);
}

.pf-value {
  font-size: 13px;
  line-height: 1.5;
  color: var(--color-text-primary);
  word-break: break-word;
}

.pf-value.empty {
  color: var(--color-text-muted);
  font-style: italic;
}

.pf-goal-chip {
  display: inline-block;
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  padding: 2px 8px;
  margin: 2px 6px 2px 0;
  font-size: 12px;
  color: var(--color-text-primary);
}

/* ── AI 观察时间线 ── */
.pf-notes {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.pf-note {
  border: 1px solid var(--color-border);
  border-left: 3px solid var(--color-blue);
  border-radius: 0 8px 8px 0;
  background: var(--color-bg-surface);
  padding: 8px 12px;
}

.pf-note-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.pf-note-time {
  font-size: 11px;
  color: var(--color-text-muted);
  font-family: 'Cascadia Code', 'Consolas', monospace;
}

.pf-note-del {
  background: none;
  border: none;
  color: var(--color-text-muted);
  font-size: 15px;
  line-height: 1;
  padding: 0 2px;
  cursor: pointer;
}

.pf-note-del:hover {
  color: var(--color-red);
}

.pf-note-body {
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-primary);
  white-space: pre-wrap;
}

.pf-notes-empty {
  font-size: 12px;
  color: var(--color-text-muted);
  line-height: 1.6;
  margin: 0;
}

/* ── 表单编辑 ── */
.profile-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.pf-input-wrap {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}

.pf-input-label {
  font-size: 11px;
  color: var(--color-text-secondary);
}

.pf-input {
  width: 100%;
  box-sizing: border-box;
  padding: 8px 10px;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  color: var(--color-text-primary);
  font-size: 13px;
  line-height: 1.5;
}

.pf-input:focus {
  outline: none;
  border-color: var(--color-blue);
}

.pf-textarea {
  resize: vertical;
  font-family: inherit;
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

/* ── 编辑器（Markdown 源码） ── */
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
  box-sizing: border-box;
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
