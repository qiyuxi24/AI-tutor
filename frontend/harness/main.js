import { createApp, h } from 'vue'
import MessageBubble from '../src/components/MessageBubble.vue'
import '../src/style.css'

const user = { role: 'user', content: '帮我看看强化学习是什么？' }

const assistant = {
  role: 'assistant',
  content:
    '## 强化学习\n\n它是**通过与环境反复交互**来学习策略的方法。\n\n- 观察 → 动作 → 奖励\n- 目标是最大化长期回报\n',
  thinking: ['学生第一次问这个，先确认有没有监督学习基础。', '检索教材里强化学习那一章。'],
  tools: [
    { tool: 'rag_search', status: 'done', result: { duration_ms: 320 } },
    { tool: 'add_knowledge_node', status: 'done', result: { duration_ms: 180 } },
    { tool: 'mcp__websearch__web_search', status: 'error', result: { ok: false } },
  ],
}

const streaming = { role: 'assistant', content: '', tools: [], thinking: [] }

createApp({
  render: () =>
    h('div', { class: 'message-list', style: 'padding:24px;background:var(--color-bg-primary)' }, [
      h(MessageBubble, { message: user }),
      h(MessageBubble, { message: assistant }),
      h(MessageBubble, { message: streaming }),
    ]),
}).mount('#app')
