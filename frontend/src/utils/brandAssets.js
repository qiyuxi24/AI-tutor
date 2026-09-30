/**
 * brandAssets.js — public/ 静态图形资源的声明（唯一来源）
 *
 * 起因：'brand-star.svg' 这条路径此前以字面量散落在 4 个文件里（favicon / 活动栏 logo /
 * 对话 AI 头像 / 悬浮球），2026-09-28 换品牌图形时漏改了悬浮球（它内联写死一个机器人头）；
 * public/ 下还长期躺着 logo.svg、logo.jpg、icons.svg 三个无人引用的文件，光看目录无从判断。
 * → 组件一律 import 本文件的常量，不再写 '/brand-star.svg' 字面量；台账如下。
 *
 * 例外的引用点：index.html 的 <link rel="icon">（静态 HTML，先于 JS 存在，无法 import），
 * 换品牌图形时需手动同步 index.html。
 */

/** 品牌图形（像素星）：favicon、活动栏 logo、对话 AI 头像、悬浮球共用同一份 */
export const BRAND_ICON = '/brand-star.svg'

/**
 * public/ 目录全部图形资源的台账：文件 → 是什么 → 谁在用。
 * refs 为空数组 = 当前没有任何代码引用它，动它（改/删）之前先确认。
 * @type {{ file: string, usage: string, refs: string[] }[]}
 */
export const PUBLIC_ASSETS = [
  {
    file: 'brand-star.svg',
    usage: '品牌像素星（14×13、4 色、背景透明），当前唯一在用的品牌图形',
    refs: [
      'index.html（favicon）',
      'components/ActivityBar.vue（顶部 logo）',
      'components/MessageBubble.vue（对话 AI 头像）',
      'components/FloatingBall.vue（悬浮球）',
    ],
  },
  {
    file: 'logo.svg',
    usage: '早期主 Logo：三节点上升三角形 + 白色向上箭头（紫色渐变，512×512）',
    refs: [],
  },
  {
    file: 'logo.jpg',
    usage: 'logo.svg 的栅格导出（1024×1024），需要位图时用',
    refs: [],
  },
  {
    file: 'icons.svg',
    usage: '社交图标 sprite（GitHub / Discord / X / 文档），界面当前未使用',
    refs: [],
  },
]
