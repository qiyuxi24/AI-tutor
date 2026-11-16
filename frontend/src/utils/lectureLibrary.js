/**
 * lectureLibrary.js — 课程库侧栏的讲义目录合并（纯函数，无 Vue / IO，可直接单测）
 *
 * 侧栏要回答"我有哪些讲义"，但讲义信息散在两处：
 *   · KB 目录树：全量、标题权威，但不带导入时的字数/时长；
 *   · 导入队列：只留最近 20 个任务，却有刚导完那一集的字数/时长。
 * 所以按 docId 合并：树打底（含队列窗口外的老讲义），队列补充数与时长。
 *
 * 为什么不用队列当唯一来源：listLectureTasks 只返回最近 20 个任务，导过的老讲义
 * 会掉出窗口 —— 侧栏若只认队列，用户就"找不到上周看的那一集"。
 */
import { formatClock } from './lecture.js'

/**
 * @param tasks 队列快照（每条 task 带 items[]）
 * @param kbLectures KB 树里的结构化讲义 [{ docId, title }]
 * @returns [{ docId, title, meta }]，按 KB 树顺序在前、队列独有项在后
 */
export function buildLectureDirectory(tasks = [], kbLectures = []) {
  const fromTasks = new Map()
  for (const task of tasks) {
    for (const item of task.items || []) {
      if (item.status !== 'done' || !item.node_id) continue
      const docId = Number(item.node_id)
      fromTasks.set(docId, {
        docId,
        title: item.title || item.url || '未命名讲义',
        meta: [item.chars ? `${item.chars} 字` : '', formatClock(item.duration_secs)]
          .filter(Boolean).join(' · '),
      })
    }
  }

  const seen = new Set()
  const out = []
  for (const k of kbLectures) {
    const docId = Number(k.docId)
    if (!docId || seen.has(docId)) continue
    seen.add(docId)
    out.push({ docId, title: k.title || '未命名讲义', meta: fromTasks.get(docId)?.meta || '' })
  }
  for (const [docId, item] of fromTasks) {
    if (seen.has(docId)) continue
    seen.add(docId)
    out.push(item)
  }
  return out
}
