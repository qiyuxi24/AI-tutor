/**
 * slideAssets.js — 课堂截图（`/api/v1/kb/slides/…`）的鉴权图片加载
 *
 * 为什么需要这一层：截图接口要 JWT，而 `<img src>` 发不出 Authorization 头（token 只在
 * axios 拦截器里）。两步走：axios 取 blob（带鉴权）→ `URL.createObjectURL` 换成浏览器能直接
 * 渲染的地址。这个写法来自 `components/KbTextPreviewDialog.vue`（讲义预览的截图网格），
 * 课时页的时间轴缩略图与正文大图是同一种需求，所以抽成一份而不是各写一遍
 * —— 各自写一份的代价是 revoke 漏在任何一处都会漏内存。
 *
 * 生命周期归调用方：`onBeforeUnmount` 里自动 revoke 全部 objectURL（组件卸载后地址就废了，
 * 不 revoke 等于把整批截图钉在内存里，切几次课时页就明显了）。
 *
 * 用法：
 *   const { urls, failed, load } = useSlideAssets()
 *   watch(() => props.segments.map(s => s.slide_url), (list) => load(list), { immediate: true })
 *   // 模板里： urls[seg.slide_url] 非空才 <img :src="…">
 */
import { onBeforeUnmount, ref } from 'vue'
import { apiClient } from '../api'

export function useSlideAssets() {
  const urls = ref({})     // 接口地址 → objectURL（只存已加载成功的）
  const failed = ref({})   // 接口地址 → true（加载失败，调用方据此不占位）
  let alive = true
  const created = []

  /**
   * 批量加载。**并发拉取但不并发覆盖**：某批先回来就先把它们挂上去，
   * 而不是等全齐（时间轴首屏只等第一屏缩略图，长课时不该被最后一张图卡住）。
   */
  async function load(list) {
    const srcs = [...new Set((list || []).filter(Boolean))]
    const batchUrls = {}
    const batchFailed = {}
    await Promise.all(srcs.map(async (src) => {
      // 已加载过（同一节课反复切）不重复请求
      if (urls.value[src]) { batchUrls[src] = urls.value[src]; return }
      try {
        const { data } = await apiClient.get(src, { responseType: 'blob' })
        if (!alive) return
        const url = URL.createObjectURL(data)
        created.push(url)
        batchUrls[src] = url
      } catch {
        batchFailed[src] = true
      }
    }))
    if (!alive) return
    urls.value = { ...urls.value, ...batchUrls }
    failed.value = { ...failed.value, ...batchFailed }
  }

  /** 手动释放（换课时 / 换一批截图时先清，避免旧图继续占内存） */
  function revoke() {
    while (created.length) URL.revokeObjectURL(created.pop())
    urls.value = {}
    failed.value = {}
  }

  onBeforeUnmount(() => {
    alive = false
    revoke()
  })

  return { urls, failed, load, revoke }
}
