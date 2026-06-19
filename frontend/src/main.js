import { createApp } from 'vue'
import { createPinia } from 'pinia'
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
app.directive('click-outside', clickOutside)
app.mount('#app')
