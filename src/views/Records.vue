<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { postJSON } from '../api'

interface Record {
  uuid: string
  repo_full: string
  issue_number: number
  issue_url: string
  title: string
  state: string
  pr_url: string
  error: string
  trigger_word: string
  created_at: string
  updated_at: string
}

const records = ref<Record[]>([])
const filter = ref('')
const err = ref('')

const STATES = ['', 'wait', 'ready', 'ok', 'putout', 'failed'] as const

async function refresh() {
  err.value = ''
  try {
    const j = await postJSON<{ records: Record[] }>('/api/records', { state: filter.value || undefined })
    records.value = j.records
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

function stateText(s: string): string {
  const map: Record<string, string> = {
    wait: '等待下发',
    ready: '已下发',
    ok: '已完成',
    putout: '已放弃',
    failed: '失败',
  }
  return map[s] || s
}

onMounted(refresh)
</script>

<template>
  <div class="records">
    <div v-if="err" class="alert">{{ err }}</div>
    <div class="row toolbar">
      <span class="muted">状态过滤</span>
      <select v-model="filter" @change="refresh">
        <option v-for="s in STATES" :key="s" :value="s">{{ s || '全部' }}</option>
      </select>
      <button @click="refresh">刷新</button>
    </div>

    <div class="panel table-panel">
      <table v-if="records.length" class="list">
        <thead>
          <tr>
            <th>时间</th>
            <th>仓库 / Issue</th>
            <th>标题</th>
            <th>触发词</th>
            <th>状态</th>
            <th>结果</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in records" :key="r.uuid">
            <td class="muted nowrap">{{ r.created_at }}</td>
            <td class="mono">
              {{ r.repo_full }}#{{ r.issue_number }}
              <a :href="r.issue_url" target="_blank">open</a>
            </td>
            <td>{{ r.title }}</td>
            <td class="muted">{{ r.trigger_word }}</td>
            <td><span :class="['status-tag', r.state]">{{ stateText(r.state) }}</span></td>
            <td>
              <a v-if="r.pr_url" :href="r.pr_url" target="_blank">查看 PR</a>
              <span v-else-if="r.error" class="muted" :title="r.error">{{ r.error.slice(0, 50) }}</span>
              <span v-else class="muted">—</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">暂无记录。</p>
    </div>
  </div>
</template>

<style scoped>
.toolbar {
  margin-bottom: 12px;
}

select {
  width: 160px;
}

.table-panel {
  overflow-x: auto;
}

.nowrap {
  white-space: nowrap;
}
</style>
