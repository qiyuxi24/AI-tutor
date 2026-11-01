<script setup>
/**
 * LecturePane.vue — 设置 · 视频课程导入
 *
 * 取词方式 / 合集展开 / 幻灯片截图 / 字幕语言 / 转写模型都收在这里：
 * 导入页只负责「贴链接 → 加入队列」，不再每次都问一遍。
 * 值为本机偏好（localStorage，见 `utils/lectureSettings.js`），不入后端。
 */
import { computed } from 'vue'
import { TRANSCRIPT_SOURCES, useLectureSettings } from '../../utils/lectureSettings'

const settings = useLectureSettings()

const currentSource = computed(
  () => TRANSCRIPT_SOURCES.find((s) => s.key === settings.transcriptSource) || TRANSCRIPT_SOURCES[0],
)
</script>

<template>
  <section class="sc-section">
    <h3>视频课程导入</h3>
    <p class="usage-hint">
      这里的选项决定导入页怎么取词、要不要展开合集与截图。改一次即全局生效（仅本机浏览器）。
    </p>

    <div class="sc-row">
      <div class="sc-row-info">
        <div class="sc-row-title">取词方式</div>
        <div class="sc-row-desc">{{ currentSource.desc }}</div>
      </div>
      <div class="theme-toggle-group">
        <button
          v-for="opt in TRANSCRIPT_SOURCES"
          :key="opt.key"
          type="button"
          class="theme-option"
          :class="{ active: settings.transcriptSource === opt.key }"
          :title="opt.desc"
          @click="settings.transcriptSource = opt.key"
        >{{ opt.title }}</button>
      </div>
    </div>

    <div class="sc-row">
      <div class="sc-row-info">
        <div class="sc-row-title">合集全量</div>
        <div class="sc-row-desc">B 站多 P / 合集的链接一次导入全部集数，落进以合集命名的文件夹</div>
      </div>
      <el-switch v-model="settings.expand" />
    </div>

    <div class="sc-row">
      <div class="sc-row-info">
        <div class="sc-row-title">幻灯片截图</div>
        <div class="sc-row-desc">画面变化时抽帧去重，图片插到对应讲解段落前（需下载视频，慢一些）</div>
      </div>
      <el-switch v-model="settings.slides" />
    </div>

    <div class="sc-row sc-row--stack">
      <div class="sc-row-info">
        <div class="sc-row-title">字幕语言优先级</div>
        <div class="sc-row-desc">留空＝中文优先（zh-Hans, zh-CN, zh, en）</div>
      </div>
      <el-input
        v-model="settings.languages"
        size="small"
        class="sc-field"
        placeholder="zh-Hans, zh-CN, zh, en"
      />
    </div>

    <div class="sc-row sc-row--stack">
      <div class="sc-row-info">
        <div class="sc-row-title">转写模型</div>
        <div class="sc-row-desc">留空＝服务端 <code>.env</code> 的 <code>ASR_MODEL</code></div>
      </div>
      <el-input
        v-model="settings.asrModel"
        size="small"
        class="sc-field"
        placeholder="如 whisper-1 / qwen3-asr-flash"
      />
    </div>

    <p class="usage-hint">
      依赖：平台字幕直取只需网络；语音转写需要 ffmpeg + 转写模型；幻灯片截图还需要 yt-dlp 与 ffmpeg。
      缺什么会在导入页顶部提示。
    </p>
  </section>
</template>

<style scoped>
.sc-row--stack {
  align-items: flex-start;
}

.sc-field {
  width: 240px;
  flex-shrink: 0;
  margin-left: 16px;
}
</style>
