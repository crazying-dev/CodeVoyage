<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { state, loadStatus, isLoggedIn } from '../store'
import Overview from './Overview.vue'
import Repos from './Repos.vue'
import Workflow from './Workflow.vue'
import Records from './Records.vue'
import Settings from './Settings.vue'

const router = useRouter()
const ready = ref(false)
const tab = ref('overview')
const tabs = [
  { key: 'overview', label: 'Agent 运行' },
  { key: 'repos', label: '仓库绑定' },
  { key: 'workflow', label: 'Workflow 生成器' },
  { key: 'records', label: '任务记录' },
  { key: 'settings', label: '本地配置' },
]

onMounted(async () => {
  if (!state.loaded) await loadStatus()
  if (!isLoggedIn()) {
    router.replace('/login')
    return
  }
  ready.value = true
})
</script>

<template>
  <div v-if="ready">
    <div class="tabs">
      <button
        v-for="t in tabs"
        :key="t.key"
        :class="tab === t.key ? 'active' : ''"
        @click="tab = t.key"
      >
        {{ t.label }}
      </button>
    </div>

    <div class="content">
      <Overview v-if="tab === 'overview'" />
      <Repos v-else-if="tab === 'repos'" />
      <Workflow v-else-if="tab === 'workflow'" />
      <Records v-else-if="tab === 'records'" />
      <Settings v-else />
    </div>
  </div>
</template>

<style scoped>
.tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 18px;
  border-bottom: 1px solid var(--border);
  padding-bottom: 10px;
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

.content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
</style>
