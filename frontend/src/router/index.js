import { createRouter, createWebHashHistory } from 'vue-router'

/**
 * 路由表
 *
 * 不设 #/login 路由页与路由守卫：**登录墙由 HomeView 首屏承载** ——
 * HomeView.onMounted → authStore.ensureSession() 为 false → 弹 mandatory 的 LoginDialog。
 * 登录/注册表单也由它复用（点头像 / 设置页"切换账号"，此时可关闭）。
 * 加回登录页 = 加回路由 + 守卫 + api 层 401 跳转三处，别只加一处。
 */
const routes = [
  {
    path: '/',
    name: 'Home',
    component: () => import('../views/HomeView.vue'),
  },
  // 兜底：未知路径（老书签 #/login、手输地址）→ 回首页。
  // 没有它，vue-router 匹配不到路由就渲染空页面（白屏且无任何提示）。
  {
    path: '/:pathMatch(.*)*',
    redirect: '/',
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

export default router
