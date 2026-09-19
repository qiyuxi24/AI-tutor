import { createRouter, createWebHistory } from 'vue-router'
import Login from './views/Login.vue'
import Layout from './views/Layout.vue'
import Users from './views/Users.vue'
import Admins from './views/Admins.vue'
import AuditLogs from './views/AuditLogs.vue'

const routes = [
  { path: '/login', name: 'Login', component: Login },
  { 
    path: '/', 
    component: Layout,
    children: [
      { path: '', redirect: '/users' },
      { path: 'users', name: 'Users', component: Users },
      { path: 'admins', name: 'Admins', component: Admins },
      { path: 'audit-logs', name: 'AuditLogs', component: AuditLogs },
    ]
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to, from, next) => {
  const token = localStorage.getItem('admin_token')
  if (to.path !== '/login' && !token) {
    next('/login')
  } else if (to.path === '/login' && token) {
    next('/')
  } else {
    next()
  }
})

export default router
