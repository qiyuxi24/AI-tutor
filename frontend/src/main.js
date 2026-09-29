import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
// Element Plus 暗色主题变量（html.dark 下生效），需在自定义 style.css 之前引入，
// 以便 style.css 中覆盖的 --el-* 变量优先生效
import 'element-plus/theme-chalk/dark/css-vars.css'
// KaTeX 数学公式样式（行内/块级公式渲染必需）
import 'katex/dist/katex.min.css'
// Markdown 渲染主题（代码高亮配色，设置页可切换）：导入即应用上次选择
import './utils/mdTheme.js'
import router from './router/index.js'
import './style.css'
// 通用组件类（卡片 / 按钮 / 输入 / 侧边栏 / 标签 / 弹窗 / 空状态）
// Token 定义在 style.css，本文件只是用 Token 拼出的类，必须在 style.css 之后引入
import './styles/components.css'
// 设置页样式（容器 + 各设置面板共用）：面板是子组件，样式必须全局生效
import './styles/settings.css'
import App from './App.vue'

// 全局指令：点击外部关闭
const clickOutside = {
  mounted(el, binding) {
    el.__clickOutside = (event) => {
      if (!(el === event.target || el.contains(event.target))) {
        binding.value(event)
      }
    }
    document.addEventListener('click', el.__clickOutside)
  },
  unmounted(el) {
    document.removeEventListener('click', el.__clickOutside)
  },
}

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElementPlus)
app.directive('click-outside', clickOutside)
app.mount('#app')
