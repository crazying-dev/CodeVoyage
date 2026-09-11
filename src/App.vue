<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { state, loadStatus, isLoggedIn, logout } from './store'
import { postJSON } from './api'

const router = useRouter()
const route = useRoute()
const logged = computed(() => isLoggedIn() && route.path !== '/login')

const NAV = [
  { to: '/overview', label: 'Agent 运行' },
  { to: '/chat', label: 'AI 对话' },
  { to: '/config', label: '配置' },
  { to: '/diagnostics', label: '诊断' },
  { to: '/logs', label: '运行日志' },
  { to: '/agreement', label: '用户协议' },
]

/** 未登录一律回登录页；已登录访问 /login 则进控制台 */
async function guard() {
  if (!state.loaded) await loadStatus()
  if (!isLoggedIn()) {
    if (route.path !== '/login') router.replace('/login')
    return
  }
  if (route.path === '/login') router.replace('/overview')
}

onMounted(async () => {
  await guard()
  if (!isLoggedIn()) return
  // 登录态若已被远端拒绝，清理凭证并回登录页，避免"看着登录了却处处 401"
  try {
    const d = await postJSON<{
      logged_in: boolean
      session: { checked: boolean; valid: boolean; reason: string }
    }>('/api/local/diagnostics', {})
    if (d.logged_in && d.session?.checked && !d.session.valid) {
      await postJSON('/api/user/logout', {})
      state.error = `登录已失效：${d.session.reason}，请重新登录`
      router.replace('/login')
    }
  } catch {
    /* 网络异常时不强制退出 */
  }
})

watch(() => route.path, guard)

async function doLogout() {
  try {
    await logout()
  } catch {
    /* ignore */
  }
  router.replace('/login')
}
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <span class="logo">CodeVoyage</span>
    </div>
    <nav v-if="logged">
      <RouterLink v-for="item in NAV" :key="item.to" :to="item.to">{{ item.label }}</RouterLink>
    </nav>
    <div class="spacer"></div>
    <nav>
      <template v-if="state.status?.email">
        <span class="email">{{ state.status?.email }}</span>
        <button class="ghost" @click="doLogout">退出登录</button>
      </template>
    </nav>
  </header>
  <main class="page">
    <RouterView />
  </main>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 0 20px;
  height: 54px;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
}

.brand {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.logo {
  font-size: 20px;
  font-weight: 600;
}

nav {
  display: flex;
  align-items: center;
  gap: 6px;
}

nav a {
  color: var(--text-dim);
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 14px;
}

nav a.router-link-active {
  color: var(--text);
  background: var(--panel-2);
}

.email {
  color: var(--text-dim);
  font-size: 13px;
}

button.ghost {
  background: transparent;
  border-color: var(--border);
}

.page {
  padding: 22px;
  max-width: 1320px;
  margin: 0 auto;
}
</style>
