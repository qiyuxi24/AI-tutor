/**
 * lectureSettings.js — 视频课程导入偏好（纯前端、localStorage）
 *
 * 为什么从导入页挪到设置：取词方式 / 合集展开 / 幻灯片截图是**一次配好、长期复用**的
 * 偏好，不是每次导入都要重新做的决定。导入页只留「贴链接 → 加入队列」。
 *
 * 与 `graphLayout.js` 同一套模式：模块级单例 —— 设置页与导入页读同一份响应式状态，
 * 设置页一改、导入页立即生效；只存本机浏览器，不入后端、不按用户隔离。
 */

import { reactive, watch } from 'vue'

const STORAGE_KEY = 'ai_tutor_lecture_import'

/** 取词方式（key 与后端 `video_import.TRANSCRIPT_SOURCES` 一致） */
export const TRANSCRIPT_SOURCES = [
  { key: 'auto', title: '自动', desc: '字幕优先；没有字幕才语音转写，最省额度' },
  { key: 'subtitle', title: '仅字幕', desc: '没有字幕直接报错，不花转写额度' },
  { key: 'asr', title: '语音转写', desc: '跳过字幕，直接从音频转写（慢、按量计费）' },
]

const KEYS = TRANSCRIPT_SOURCES.map((s) => s.key)

const DEFAULTS = {
  transcriptSource: 'auto',
  expand: true,      // 合集全量：B 站多 P / ugc_season 展开成全部集数
  slides: false,     // 幻灯片截图：要下载视频，默认关
  languages: '',     // 字幕语言优先级，留空用后端默认
  asrModel: '',      // 转写模型，留空用 .env 的 ASR_MODEL
}

function load() {
  const out = { ...DEFAULTS }
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
    if (KEYS.includes(saved.transcriptSource)) out.transcriptSource = saved.transcriptSource
    out.expand = saved.expand !== false
    out.slides = saved.slides === true
    out.languages = typeof saved.languages === 'string' ? saved.languages : ''
    out.asrModel = typeof saved.asrModel === 'string' ? saved.asrModel : ''
  } catch {
    // 存储损坏 / 隐私模式：用默认值，本次会话仍可正常导入
  }
  return out
}

const settings = reactive(load())

watch(settings, (value) => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(value))
  } catch {
    // 写入失败只在本次会话生效，不影响导入
  }
}, { deep: true })

export function useLectureSettings() {
  return settings
}

/** 字幕语言输入 → 数组（逗号/顿号/空白分隔；空串 → []，交给后端默认值） */
export function subtitleLanguages(value = settings.languages) {
  return String(value || '').split(/[,，\s]+/).filter(Boolean)
}
