<script setup>
/**
 * EditDialog.vue — 知识图谱编辑弹窗组件
 *
 * 根据 mode 动态显示不同表单：
 *   - create-node:    名称、标签、初始内容
 *   - edit-node:      名称、标签
 *   - add-edge-from-node: 目标节点下拉、关系类型、标签
 *   - edit-edge:      关系类型、标签
 *
 * Props:
 *   visible: Boolean       是否显示
 *   mode: String           编辑模式
 *   data: Object           预填充数据
 *   nodeOptions: Array     节点 ID 选项列表（用于边的目标节点下拉）
 *
 * Emits:
 *   close: ()              关闭弹窗
 *   submit: (formData)     提交表单数据
 */

import { ref, reactive, watch, computed, onMounted } from 'vue'

/* ================================================================
   Props
   ================================================================ */
const props = defineProps({
  visible: { type: Boolean, default: false },
  /** 'create-node' | 'edit-node' | 'edit-edge' */
  mode: { type: String, default: 'create-node' },
  /** 预填充数据 */
  data: { type: Object, default: () => ({}) },
})

/* ================================================================
   Emits
   ================================================================ */
const emit = defineEmits(['close', 'submit'])

/* ================================================================
   表单状态
   ================================================================ */
const form = reactive({
  name: '',
  tags: '',
  content: '',
  relation: 'related',
  label: '',
})

/* ================================================================
   弹窗标题 & 按钮文案
   ================================================================ */
const title = computed(() => {
  switch (props.mode) {
    case 'create-node': return '创建新节点'
    case 'edit-node': return '编辑节点信息'
    case 'edit-edge': return '编辑边信息'
    default: return '编辑'
  }
})

const submitLabel = computed(() => {
  switch (props.mode) {
    case 'create-node': return '创建'
    default: return '保存'
  }
})

/* ================================================================
   监听 data 变化 → 填充表单
   ================================================================ */
watch(() => [props.visible, props.data, props.mode], () => {
  if (!props.visible) return

  // 重置表单
  form.name = ''
  form.tags = ''
  form.content = ''
  form.relation = 'related'
  form.label = ''

  const d = props.data || {}

  switch (props.mode) {
    case 'create-node':
      // 新节点无预填
      break

    case 'edit-node':
      form.name = d.name || ''
      form.tags = (d.tags || []).join(', ')
      break

    case 'edit-edge':
      form.relation = d.relation || 'related'
      form.label = d.label || ''
      break
  }
})

/* ================================================================
   表单提交
   ================================================================ */
function handleSubmit() {
  const payload = {}

  switch (props.mode) {
    case 'create-node':
      payload.name = form.name.trim()
      payload.tags = form.tags.split(',').map(t => t.trim()).filter(Boolean)
      payload.content = form.content || undefined
      if (!payload.name) return alert('请输入节点名称')
      break

    case 'edit-node':
      payload.name = form.name.trim()
      payload.tags = form.tags.split(',').map(t => t.trim()).filter(Boolean)
      if (!payload.name) return alert('请输入节点名称')
      break

    case 'edit-edge':
      payload.relation = form.relation
      payload.label = form.label.trim()
      break
  }

  emit('submit', payload)
  emit('close')
}

function handleCancel() {
  emit('close')
}
</script>

<template>
  <Teleport to="body">
    <!-- 半透明遮罩 -->
    <div v-if="visible" class="dialog-backdrop" @click.self="handleCancel">
      <div class="dialog-panel" @click.stop>
        <!-- 标题栏 -->
        <div class="dialog-header">
          <h3 class="dialog-title">{{ title }}</h3>
          <button class="dialog-close" @click="handleCancel">×</button>
        </div>

        <!-- 表单区域 -->
        <div class="dialog-body">
          <!-- ── 创建节点 / 编辑节点：名称 ── -->
          <div
            v-if="mode === 'create-node' || mode === 'edit-node'"
            class="form-group"
          >
            <label class="form-label">节点名称</label>
            <input
              v-model="form.name"
              class="form-input"
              type="text"
              placeholder="例如：二叉树遍历"
            />
          </div>

          <!-- ── 创建节点 / 编辑节点：标签 ── -->
          <div
            v-if="mode === 'create-node' || mode === 'edit-node'"
            class="form-group"
          >
            <label class="form-label">标签（逗号分隔）</label>
            <input
              v-model="form.tags"
              class="form-input"
              type="text"
              placeholder="例如：数据结构, 二级"
            />
          </div>

          <!-- ── 创建节点：初始内容 ── -->
          <div v-if="mode === 'create-node'" class="form-group">
            <label class="form-label">初始内容（可选）</label>
            <textarea
              v-model="form.content"
              class="form-textarea"
              rows="4"
              placeholder="Markdown 格式内容..."
            ></textarea>
          </div>

          <!-- ── 编辑边：关系类型 ── -->
          <div
            v-if="mode === 'edit-edge'"
            class="form-group"
          >
            <label class="form-label">关系类型</label>
            <select v-model="form.relation" class="form-select">
              <option value="prerequisite">prerequisite（前置依赖）</option>
              <option value="related">related（相关）</option>
              <option value="confusion">confusion（易混淆）</option>
              <option value="extension">extension（扩展）</option>
            </select>
          </div>

          <!-- ── 编辑边：标签 ── -->
          <div
            v-if="mode === 'edit-edge'"
            class="form-group"
          >
            <label class="form-label">关系说明</label>
            <input
              v-model="form.label"
              class="form-input"
              type="text"
              placeholder="例如：需要先理解递归定义"
            />
          </div>
        </div>

        <!-- 按钮区 -->
        <div class="dialog-footer">
          <button class="btn btn-cancel" @click="handleCancel">取消</button>
          <button class="btn btn-primary" @click="handleSubmit">{{ submitLabel }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* ================================================================
   遮罩 + 弹窗容器
   ================================================================ */
.dialog-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(2px);
}

.dialog-panel {
  background: #1e1e2e;
  border: 1px solid #313244;
  border-radius: 12px;
  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.5);
  width: 420px;
  max-width: 90vw;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ================================================================
   标题栏
   ================================================================ */
.dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid #313244;
}

.dialog-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #e5e7eb;
}

.dialog-close {
  background: none;
  border: none;
  color: #9ca3af;
  font-size: 20px;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
  transition: color 0.15s;
}

.dialog-close:hover {
  color: #e5e7eb;
}

/* ================================================================
   表单区域
   ================================================================ */
.dialog-body {
  padding: 20px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-label {
  font-size: 13px;
  font-weight: 500;
  color: #a6adc8;
}

.form-hint {
  font-size: 11px;
  color: #6b7280;
  font-weight: 400;
}

.form-input,
.form-select,
.form-textarea {
  background: #11111b;
  border: 1px solid #313244;
  border-radius: 6px;
  color: #e5e7eb;
  padding: 8px 12px;
  font-size: 13px;
  font-family: inherit;
  transition: border-color 0.15s;
}

.form-input:focus,
.form-select:focus,
.form-textarea:focus {
  outline: none;
  border-color: #60a5fa;
}

.form-textarea {
  resize: vertical;
  min-height: 80px;
}

.form-select[size] {
  padding: 4px;
}

.form-select[size] option {
  padding: 6px 8px;
  border-radius: 4px;
}

/* ================================================================
   按钮区
   ================================================================ */
.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 14px 20px;
  border-top: 1px solid #313244;
}

.btn {
  padding: 8px 20px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid transparent;
  transition: background 0.15s, border-color 0.15s;
}

.btn-cancel {
  background: transparent;
  color: #a6adc8;
  border-color: #313244;
}

.btn-cancel:hover {
  background: #313244;
  color: #e5e7eb;
}

.btn-primary {
  background: #60a5fa;
  color: #11111b;
  border-color: #60a5fa;
}

.btn-primary:hover {
  background: #3b82f6;
  border-color: #3b82f6;
}
</style>
