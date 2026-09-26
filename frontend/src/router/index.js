import { createRouter, createWebHashHistory } from 'vue-router'
import { useAuthStore } from '../stores/authStore'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/LoginView.vue'),
    meta: { guest: true },
  },
  {
    path: '/',
    name: 'Home',
    component: () => import('../views/HomeView.vue'),
    meta: { requiresAuth: true },
  },
  // 兜底：未知路径（老书签 #/chat、手输地址、LoginView 回跳的非法 redirect）→ 回首页。
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

// 路由守卫：未登录跳转登录页
router.beforeEach((to, from, next) => {
  const authStore = useAuthStore()

  if (to.meta.requiresAuth && !authStore.isLoggedIn) {
    next({ name: 'Login', query: { redirect: to.fullPath } })
  } else if (to.meta.guest && authStore.isLoggedIn) {
    next({ name: 'Home' })
  } else {
    next()
  }
})

export default router
