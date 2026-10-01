<script setup>
/**
 * QuizCard.vue — 对话内的题目小卡片（点选项 → 提交）
 *
 * 为什么做成卡片（而不是把题目拼成 markdown 文本）：
 *   ① 学生不用手打 "B" —— 点选项就能作答，作答路径短、不会打错字；
 *   ② 题干与选项有明确边界，不被 markdown 渲染吃掉缩进/换行；
 *   ③ 提交后就地锁住，避免同一题反复提交。
 *
 * 判分**不在前端做**：提交时以一条**纯答案**消息发出去（`A` / `AC` / `对` / `栈`），
 * 由后端 `auto_grade_pending` 反查"最近一道待作答的题"做规则判分（0 token）。
 * 这里不能本地判对错 —— 推送载荷刻意不含 answer / analysis
 * （答案留在后端，见 `chat_quiz._public_question`），前端根本拿不到。
 */
import { ref } from 'vue'

const props = defineProps({
  questions: { type: Array, default: () => [] },
})

const emit = defineEmits(['submit'])

const picked = ref({})     // question_id → 单选/判断/填空的值；多选 → 数组
const sent = ref({})       // question_id → 已提交的答案文本（有值即锁定）

const TYPE_LABELS = {
  single: '单选', multiple: '多选', judge: '判断', fill: '填空', short_answer: '简答',
}

function typeLabel(t) {
  return TYPE_LABELS[t] || t
}

function toggleMulti(qid, value) {
  const cur = Array.isArray(picked.value[qid]) ? [...picked.value[qid]] : []
  const i = cur.indexOf(value)
  if (i >= 0) cur.splice(i, 1)
  else cur.push(value)
  picked.value = { ...picked.value, [qid]: cur }
}

/** 作答文本：多选排好序拼成 "AC"（与后端 grader 的字母提取口径一致） */
function answerText(q) {
  const v = picked.value[q.id]
  if (Array.isArray(v)) return v.slice().sort().join('')
  return (v || '').toString().trim()
}

function hasAnswer(q) {
  return answerText(q).length > 0
}

function submit(q) {
  const text = answerText(q)
  if (!text || sent.value[q.id]) return
  sent.value = { ...sent.value, [q.id]: text }
  emit('submit', { questionId: q.id, answer: text })
}
</script>

<template>
  <div class="quiz-cards">
    <div v-for="(q, i) in questions" :key="q.id || i" class="qc" :class="{ 'is-sent': sent[q.id] }">
      <div class="qc-head">
        <span class="qc-idx">第 {{ i + 1 }} 题</span>
        <span class="qc-type">{{ typeLabel(q.type) }}</span>
        <span v-if="sent[q.id]" class="qc-sent">已提交：{{ sent[q.id] }}</span>
      </div>

      <p class="qc-question">{{ q.question }}</p>

      <!-- 单选 -->
      <div v-if="q.type === 'single'" class="qc-options">
        <label
          v-for="o in q.options"
          :key="o.value"
          class="qc-option"
          :class="{ on: picked[q.id] === o.value }"
        >
          <input
            type="radio"
            :name="'qc_' + q.id"
            :value="o.value"
            :disabled="!!sent[q.id]"
            :checked="picked[q.id] === o.value"
            @change="picked = { ...picked, [q.id]: o.value }"
          >
          <span class="qc-key">{{ o.value }}</span>
          <span class="qc-label">{{ o.label }}</span>
        </label>
      </div>

      <!-- 多选 -->
      <div v-else-if="q.type === 'multiple'" class="qc-options">
        <label
          v-for="o in q.options"
          :key="o.value"
          class="qc-option"
          :class="{ on: (picked[q.id] || []).includes(o.value) }"
        >
          <input
            type="checkbox"
            :value="o.value"
            :disabled="!!sent[q.id]"
            :checked="(picked[q.id] || []).includes(o.value)"
            @change="toggleMulti(q.id, o.value)"
          >
          <span class="qc-key">{{ o.value }}</span>
          <span class="qc-label">{{ o.label }}</span>
        </label>
      </div>

      <!-- 判断：后端 judge 题不保证带 options，固定给「对 / 错」两个按钮 -->
      <div v-else-if="q.type === 'judge'" class="qc-options qc-inline">
        <button
          v-for="o in ['对', '错']"
          :key="o"
          type="button"
          class="qc-option as-btn"
          :class="{ on: picked[q.id] === o }"
          :disabled="!!sent[q.id]"
          @click="picked = { ...picked, [q.id]: o }"
        >{{ o }}</button>
      </div>

      <!-- 填空 -->
      <input
        v-else-if="q.type === 'fill'"
        v-model="picked[q.id]"
        class="qc-input"
        type="text"
        placeholder="写出你的答案"
        :disabled="!!sent[q.id]"
        @keyup.enter="submit(q)"
      >

      <!-- 简答（对话内出题不使用，兜底） -->
      <textarea
        v-else
        v-model="picked[q.id]"
        class="qc-input"
        rows="2"
        placeholder="写出你的答案"
        :disabled="!!sent[q.id]"
      ></textarea>

      <div class="qc-actions">
        <button
          type="button"
          class="qc-submit"
          :disabled="!!sent[q.id] || !hasAnswer(q)"
          @click="submit(q)"
        >{{ sent[q.id] ? '已提交' : '提交' }}</button>
        <span v-if="!sent[q.id] && !hasAnswer(q)" class="qc-hint">先作答再提交</span>
        <span v-else-if="sent[q.id]" class="qc-hint">等 AI 判分，结果会出现在下面</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.quiz-cards {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 10px;
}

.qc {
  border: 1px solid var(--color-border-default, #ddd);
  border-left: 3px solid var(--color-accent);
  border-radius: 10px;
  background: var(--color-bg-card, transparent);
  padding: 10px 12px;
}
.qc.is-sent { opacity: 0.82; border-left-color: var(--color-green, #22a06b); }

.qc-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 11px;
}
.qc-idx { font-weight: 600; color: var(--color-text-secondary); }
.qc-type {
  padding: 0 6px;
  border-radius: 9px;
  background: var(--color-accent-light, rgba(99, 102, 241, 0.12));
  color: var(--color-text-secondary);
}
.qc-sent { margin-left: auto; color: var(--color-green, #22a06b); }

.qc-question {
  margin: 0 0 8px;
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--color-text-primary);
}

.qc-options { display: flex; flex-direction: column; gap: 5px; }
.qc-options.qc-inline { flex-direction: row; }

.qc-option {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  padding: 6px 9px;
  border: 1px solid var(--color-border-subtle, #eee);
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: border-color 0.12s, background 0.12s;
}
.qc-option:hover { border-color: var(--color-border-light, #ccc); }
.qc-option.on {
  border-color: var(--color-accent);
  background: var(--color-accent-light, rgba(99, 102, 241, 0.1));
  color: var(--color-text-primary);
}
.qc-option input { margin: 2px 0 0; cursor: pointer; }
.qc-option.as-btn {
  font-family: inherit;
  padding: 4px 16px;
  cursor: pointer;
}
.qc-key { font-weight: 600; }
.qc-label { flex: 1; }

.qc-input {
  width: 100%;
  box-sizing: border-box;
  padding: 6px 9px;
  border: 1px solid var(--color-border-subtle, #eee);
  border-radius: 8px;
  background: transparent;
  color: var(--color-text-primary);
  font-family: inherit;
  font-size: 13px;
  resize: vertical;
}
.qc-input:focus { outline: none; border-color: var(--color-accent); }

.qc-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 9px;
}
.qc-submit {
  font-family: inherit;
  font-size: 12.5px;
  padding: 4px 16px;
  border-radius: 8px;
  border: 1px solid var(--color-accent);
  background: var(--color-accent);
  color: var(--color-text-inverse, #fff);
  cursor: pointer;
}
.qc-submit:disabled { opacity: 0.5; cursor: not-allowed; }
.qc-hint { font-size: 11px; color: var(--color-text-tertiary); }
</style>
