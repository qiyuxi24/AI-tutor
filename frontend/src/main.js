import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
// Element Plus 暗色主题变量（html.dark 下生效），需在自定义 style.css 之前引入，
// 以便 style.css 中覆盖的 --el-* 变量优先生效
import 'element-plus/theme-chalk/dark/css-vars.css'
// KaTeX 数学公式样式（行内/块级公式渲染必需）
import 'katex/dist/katex.min.css'
import router from './router/index.js'
import './style.css'
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
