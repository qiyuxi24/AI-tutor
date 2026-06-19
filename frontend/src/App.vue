<script setup>
import { onMounted } from 'vue'
import { useAuthStore } from './stores/authStore'

const authStore = useAuthStore()

onMounted(async () => {
  // 初始化时检查 token 是否有效
  if (authStore.isLoggedIn) {
    await authStore.checkAuth()
  }
})
</script>

<template>
  <router-view />
</template>