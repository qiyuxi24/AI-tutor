<script setup>
/**
 * EditDialog.vue — 知识图谱编辑弹窗组件
 *
 * 根据 mode 动态显示不同表单：
 *   - create-node:    名称、标签、初始内容（Markdown）
 *   - edit-node:      名称、标签
 *   - edit-edge:      关系类型、关系说明
 *
 * 注意：连线模式创建边时，实际复用的是 edit-edge 模式。
 * ForceGraph 先 emit create-edge 给父组件调用 API 创建边，
 * 等 props.edges 刷新后，再用 edit-edge 模式打开本弹窗让用户编辑关系信息。
 *
 * Props:
 *   visible: Boolean       是否显示
 *   mode: String           编辑模式（'create-node' | 'edit-node' | 'edit-edge'）
 *   data: Object           预填充数据
 *
 * Emits:
 *   close: ()              关闭弹窗
 *   submit: (formData)     提交表单数据
 */

import { reactive, watch, computed } from 'vue'

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
              <option value="prerequisite">前置知识</option>
              <option value="related">相关概念</option>
              <option value="confusion">易混淆</option>
              <option value="extension">扩展延伸</option>
            </select>
            <span class="form-hint">边上的标签将根据关系类型自动显示</span>
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
  background: var(--color-bg-overlay);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(2px);
}

.dialog-panel {
  background: var(--color-bg-primary);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  box-shadow: var(--shadow-popup);
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
  border-bottom: 1px solid var(--color-border);
}

.dialog-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.dialog-close {
  background: none;
  border: none;
  color: var(--color-text-secondary);
  font-size: 20px;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
  transition: color 0.15s;
}

.dialog-close:hover {
  color: var(--color-text-primary);
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
  color: var(--color-text-secondary);
}

.form-hint {
  font-size: 11px;
  color: var(--color-text-muted);
  margin-top: 2px;
}

.form-input,
.form-select,
.form-textarea {
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  color: var(--color-text-primary);
  padding: 8px 12px;
  font-size: 13px;
  font-family: inherit;
  transition: border-color 0.15s;
}

.form-input:focus,
.form-select:focus,
.form-textarea:focus {
  outline: none;
  border-color: var(--color-blue);
}

.form-textarea {
  resize: vertical;
  min-height: 80px;
}


/* ================================================================
   按钮区
   ================================================================ */
.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 14px 20px;
  border-top: 1px solid var(--color-border);
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
  color: var(--color-text-secondary);
  border-color: var(--color-border);
}

.btn-cancel:hover {
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}

.btn-primary {
  background: var(--color-blue);
  color: var(--color-bg-secondary);
  border-color: var(--color-blue);
}

.btn-primary:hover {
  background: var(--color-blue-hover);
  border-color: var(--color-blue-hover);
}
</style>
