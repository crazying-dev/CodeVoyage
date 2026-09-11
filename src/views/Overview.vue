<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { postJSON, getJSON } from '../api'
import { state } from '../store'

interface Task {
  uuid: string
  repo_full: string
  issue_number?: number
  issue_url?: string
  title?: string
  state: string
  pr_url?: string
  error?: string
  received_at?: string
  confirm_due?: number
  steps?: string[]
}

interface Overview {
  logged_in: boolean
  email: string
  state: Record<string, any>
  confirming: Task[]
  running: Task | null
  tasks: Task[]
  logs: string
}

interface LatencyProbe {
  ok: boolean
  status: number
  ms: number
  reason: string
}

const data = ref<Overview | null>(null)
const err = ref('')
const now = ref(Date.now())
const latency = ref<{ api: LatencyProbe; git: LatencyProbe } | null>(null)
const latencyAt = ref('')
let timer: number | null = null
let ticker: number | null = null
let latTimer: number | null = null

const activity = computed(() => data.value?.state)
const statusText = computed(() => {
  const s = activity.value?.status || 'idle'
  return s
})

async function refreshLatency() {
  try {
    latency.value = await postJSON('/api/local/github-latency', {})
    latencyAt.value = new Date().toLocaleTimeString()
  } catch {
    /* 探测失败时保留上一次结果 */
  }
}

function latClass(p?: LatencyProbe): string {
  if (!p || !p.ok) return 'failed'
  if (p.ms < 400) return 'ok'
  if (p.ms < 1200) return 'waiting'
  return 'failed'
}

function latText(p?: LatencyProbe): string {
  if (!p) return '检测中'
  return p.ok ? `${p.ms} ms` : '不通'
}

async function refresh() {
  try {
    err.value = ''
    data.value = await getJSON<Overview>('/api/Agent/overview')
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

async function decide(uuid: string, yes: boolean) {
  err.value = ''
  try {
    await postJSON('/api/Agent/task/decision', { uuid, decision: yes ? 'yes' : 'no' })
    await refresh()
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

function remain(t: Task): number {
  if (!t.confirm_due) return 0
  return Math.max(0, Math.ceil(t.confirm_due - now.value / 1000))
}

onMounted(() => {
  refresh()
  refreshLatency()
  timer = window.setInterval(refresh, 2000)
  ticker = window.setInterval(() => (now.value = Date.now()), 1000)
  latTimer = window.setInterval(refreshLatency, 10000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  if (ticker) clearInterval(ticker)
  if (latTimer) clearInterval(latTimer)
  timer = null
  ticker = null
  latTimer = null
})
</script>

<template>
  <div v-if="!data && !err" class="panel muted">正在加载 Agent 状态…</div>
  <div v-if="!data && err" class="alert">{{ err }}</div>
  <div class="overview" v-if="data">
    <div class="card-grid">
      <div class="panel stat">
        <div class="stat-label">账号</div>
        <div class="stat-val">{{ data.email || state.status?.email || '未登录' }}</div>
        <div class="hint">远端：{{ state.status?.remote }}</div>
      </div>
      <div class="panel stat">
        <div class="stat-label">Agent 状态</div>
        <div class="stat-val">
          <span :class="['status-tag', statusText]">{{ statusText }}</span>
        </div>
        <div class="hint">{{ activity?.note || '空闲等待新任务' }}</div>
      </div>
      <div class="panel stat">
        <div class="stat-label">本地能力</div>
        <div class="row stat-val small">
          <span class="ok-dot" :class="{ on: state.status?.github_configured }">GitHub</span>
          <span class="ok-dot" :class="{ on: state.status?.llm_configured }">LLM</span>
        </div>
        <div class="hint">模型：{{ state.status?.llm_model || '未设置' }}</div>
      </div>
      <div class="panel stat">
        <div class="stat-label">GitHub 延迟</div>
        <div class="row stat-val small">
          <span :class="['status-tag', latClass(latency?.api)]" :title="latency?.api?.reason || ''">
            API {{ latText(latency?.api) }}
          </span>
          <span :class="['status-tag', latClass(latency?.git)]" :title="latency?.git?.reason || ''">
            Git {{ latText(latency?.git) }}
          </span>
        </div>
        <div class="hint">{{ latencyAt ? `最近检测 ${latencyAt}，每 10 秒刷新` : '正在检测…' }}</div>
      </div>
    </div>

    <div v-if="err" class="alert">{{ err }}</div>

    <template v-if="data.confirming && data.confirming.length">
      <h3>待确认任务（10 秒未操作将自动执行）</h3>
      <div v-for="t in data.confirming" :key="t.uuid" class="panel confirm-card">
        <div class="row">
          <span class="status-tag confirming">等待确认</span>
          <strong>{{ t.repo_full }}#{{ t.issue_number }}</strong>
          <span class="muted">倒计时 {{ remain(t) }}s</span>
        </div>
        <p class="title">{{ t.title }}</p>
        <a :href="t.issue_url" target="_blank">{{ t.issue_url }}</a>
        <div class="row actions">
          <button class="primary" @click="decide(t.uuid, true)">执行</button>
          <button class="danger" @click="decide(t.uuid, false)">放弃</button>
        </div>
      </div>
    </template>

    <template v-if="data.running">
      <h3>正在执行</h3>
      <div class="panel">
        <div class="row">
          <span class="status-tag running">运行中</span>
          <strong>{{ data.running.repo_full }}#{{ data.running.issue_number }}</strong>
        </div>
        <p class="title">{{ data.running.title }}</p>
        <div v-if="activity?.note" class="hint">当前步骤：{{ activity.note }}</div>
      </div>
    </template>

    <h3>最近任务</h3>
    <div class="panel table-panel">
      <table v-if="data.tasks.length" class="list">
        <thead>
          <tr>
            <th>状态</th>
            <th>仓库 / Issue</th>
            <th>标题</th>
            <th>Pull Request</th>
            <th>接收时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in data.tasks.slice(0, 20)" :key="t.uuid">
            <td><span :class="['status-tag', t.state]">{{ t.state }}</span></td>
            <td class="mono">
              {{ t.repo_full }}#{{ t.issue_number }}
              <a v-if="t.issue_url" :href="t.issue_url" target="_blank">open</a>
            </td>
            <td>{{ t.title }}</td>
            <td>
              <a v-if="t.pr_url" :href="t.pr_url" target="_blank">PR</a>
              <span v-else-if="t.error" class="hint" :title="t.error">{{ t.error.slice(0, 40) }}</span>
            </td>
            <td class="muted">{{ t.received_at }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">暂无任务。仓库有新 Issue 且命中关键词后，会出现在这里。</p>
    </div>

    <h3>运行日志</h3>
    <pre class="log">{{ data.logs || '（无日志）' }}</pre>
  </div>
</template>

<style scoped>
.stat-label {
  color: var(--text-dim);
  font-size: 12px;
  margin-bottom: 6px;
}

.stat-val {
  font-size: 16px;
  font-weight: 600;
}

.stat-val.small {
  gap: 8px;
  font-weight: 500;
}

.ok-dot {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 20px;
  border: 1px solid var(--danger);
  color: var(--danger);
}

.ok-dot.on {
  border-color: var(--ok);
  color: var(--ok);
}

h3 {
  margin: 18px 0 8px;
  font-size: 15px;
}

.confirm-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 10px;
  border-color: var(--accent);
}

.title {
  font-size: 14px;
}

.actions {
  margin-top: 4px;
}

.table-panel {
  overflow-x: auto;
}
</style>
