<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { state, logout, isLoggedIn } from './store'

const router = useRouter()
const logged = computed(() => isLoggedIn())

async function doLogout() {
  try {
    await logout()
  } catch {
    /* ignore */
  }
  router.push('/login')
}
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <span class="logo">CodeVoyage</span>
      <span class="muted sub">本地控制台 · 5431</span>
    </div>
    <div class="spacer"></div>
    <nav>
      <RouterLink to="/console">控制台</RouterLink>
      <RouterLink v-if="!logged" to="/login">登录 / 注册</RouterLink>
      <template v-else>
        <span class="email">{{ state.status?.email }}</span>
        <button class="ghost" @click="doLogout">退出登录</button>
      </template>
    </nav>
  </header>
  <main class="page">
    <RouterView />
    <p class="app-hint">
      本页面只是控制台界面：关闭浏览器标签不会停止后台运行（GetIssue / Agent / 5431 服务仍在工作）。
      退出请使用系统托盘的「退出」。
    </p>
  </main>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  gap: 12px;
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
  gap: 16px;
}

nav a {
  color: var(--text);
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
  max-width: 1180px;
  margin: 0 auto;
}

.app-hint {
  margin-top: 24px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.7;
}
</style>
