<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { state, loadStatus, isLoggedIn, ui } from '../store'
import { postJSON } from '../api'
import Overview from './Overview.vue'
import Repos from './Repos.vue'
import Records from './Records.vue'
import Logs from './Logs.vue'
import Settings from './Settings.vue'

const router = useRouter()
const ready = ref(false)

const tabs = [
  { key: 'overview', label: 'Agent 运行' },
  { key: 'repos', label: '仓库绑定' },
  { key: 'records', label: '任务记录' },
  { key: 'logs', label: '运行日志' },
  { key: 'settings', label: '本地配置' },
]

const views: Record<string, unknown> = {
  overview: Overview,
  repos: Repos,
  records: Records,
  logs: Logs,
  settings: Settings,
}

const current = computed(() => views[ui.tab] || Overview)

onMounted(async () => {
  if (!state.loaded) await loadStatus()
  if (!isLoggedIn()) {
    router.replace('/login')
    return
  }
  // 校验登录态是否仍被远端接受；已失效则清理凭证并回到登录页（避免"看起来登录了但处处 401"）
  try {
    const d = await postJSON<{
      logged_in: boolean
      session: { checked: boolean; valid: boolean; reason: string }
    }>('/api/local/diagnostics', {})
    if (d.logged_in && d.session?.checked && !d.session.valid) {
      await postJSON('/api/user/logout', {})
      state.error = `登录已失效：${d.session.reason}，请重新登录`
      router.replace('/login')
      return
    }
  } catch {
    /* 网络异常时不强制退出 */
  }
  ready.value = true
})

async function shutdown() {
  if (!confirm('确定退出 CodeVoyage 客户端吗？退出后 Agent 将不再接收任务。')) return
  try {
    await postJSON('/api/agent/shutdown', {})
  } catch {
    /* 进程退出时连接可能中断，忽略 */
  }
  alert('客户端正在关闭，可以安全关闭此页面。')
  window.close()
}
</script>

<template>
  <div v-if="ready">
    <div class="tabs">
      <button
        v-for="t in tabs"
        :key="t.key"
        :class="ui.tab === t.key ? 'active' : ''"
        @click="ui.tab = t.key"
      >
        {{ t.label }}
      </button>
      <div class="spacer"></div>
      <button class="danger" @click="shutdown">退出客户端</button>
    </div>

    <div class="content">
      <KeepAlive>
        <component :is="current" />
      </KeepAlive>
    </div>
  </div>
</template>

<style scoped>
.tabs {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 18px;
  border-bottom: 1px solid var(--border);
  padding-bottom: 10px;
  flex-wrap: wrap;
}

.tabs button {
  background: transparent;
  border-color: transparent;
  color: var(--text-dim);
  font-size: 14px;
  padding: 6px 12px;
}

.tabs button.active {
  color: var(--text);
  background: var(--panel-2);
  border-color: var(--border);
}

.tabs button.danger {
  border-color: var(--danger);
  color: var(--danger);
}

.content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
</style>
