<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { postJSON } from '../api'

interface Record_ {
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

interface TraceEvent {
  type: string
  at: string
  iteration?: number
  tool?: string
  content?: string
}

interface Trace {
  uuid: string
  repo_full: string
  issue_number: number
  title: string
  status: string
  started_at: string
  finished_at: string
  error: string
  events: TraceEvent[]
}

const records = ref<Record_[]>([])
const filter = ref('')
const err = ref('')

const trace = ref<Trace | null>(null)
const traceTitle = ref('')
const live = ref<Trace | null>(null)
let timer: number | null = null

const STATES = ['', 'wait', 'ready', 'ok', 'putout', 'failed'] as const

const EVENT_TEXT: Record<string, string> = {
  session: '复用会话',
  thinking: '思考',
  tool_call: '工具调用',
  tool_result: '工具结果',
  answer: '回答',
  note: '说明',
  error: '错误',
}

async function refresh() {
  err.value = ''
  try {
    const j = await postJSON<{ records: Record_[] }>('/api/records', { state: filter.value || undefined })
    records.value = j.records
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

async function refreshLive() {
  try {
    const j = await postJSON<{ running: boolean; trace: Trace | null }>('/api/Agent/trace/live', {})
    live.value = j.running ? j.trace : null
  } catch {
    /* 忽略实时轮询失败 */
  }
}

async function openTrace(r: Record_) {
  err.value = ''
  try {
    const j = await postJSON<{ trace: Trace }>('/api/Agent/trace', {
      repo_full: r.repo_full,
      uuid: r.uuid,
    })
    trace.value = j.trace
    traceTitle.value = `${r.repo_full}#${r.issue_number}`
  } catch (e: any) {
    trace.value = null
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

function eventText(t: string): string {
  return EVENT_TEXT[t] || t
}

onMounted(() => {
  refresh()
  refreshLive()
  timer = window.setInterval(refreshLive, 3000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  timer = null
})
</script>

<template>
  <div class="records">
    <div v-if="err" class="alert">{{ err }}</div>

    <div v-if="live" class="panel">
      <div class="row head-row">
        <span class="status-tag running">执行中</span>
        <strong>{{ live.repo_full }}#{{ live.issue_number }}</strong>
        <span class="muted small">{{ live.title }}</span>
      </div>
      <div class="events">
        <div v-for="(e, i) in live.events.slice(-40)" :key="i" class="event">
          <span :class="['status-tag', e.type === 'error' ? 'failed' : e.type === 'answer' ? 'ok' : 'waiting']">
            {{ eventText(e.type) }}
          </span>
          <span v-if="e.tool" class="mono small">{{ e.tool }}</span>
          <span class="muted small">{{ e.at }}</span>
          <pre class="content">{{ e.content }}</pre>
        </div>
        <p v-if="!live.events.length" class="muted small">刚开始执行，等待 AI 输出…</p>
      </div>
    </div>

    <div class="row toolbar">
      <span class="muted small">状态过滤</span>
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
            <th>过程</th>
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
              <span v-else-if="r.error" class="muted" :title="r.error">{{ r.error.slice(0, 40) }}</span>
              <span v-else class="muted">—</span>
            </td>
            <td><button class="ghost" @click="openTrace(r)">查看</button></td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">暂无记录。</p>
    </div>

    <div v-if="trace" class="panel">
      <div class="row head-row">
        <h3>{{ traceTitle }} 的执行过程</h3>
        <span :class="['status-tag', trace.status === 'ok' ? 'ok' : trace.status === 'failed' ? 'failed' : 'running']">
          {{ trace.status }}
        </span>
        <div class="spacer"></div>
        <button @click="trace = null">关闭</button>
      </div>
      <p class="hint">
        {{ trace.started_at }} → {{ trace.finished_at || '进行中' }}；共 {{ trace.events.length }} 条事件
      </p>
      <div class="events">
        <div v-for="(e, i) in trace.events" :key="i" class="event">
          <span :class="['status-tag', e.type === 'error' ? 'failed' : e.type === 'answer' ? 'ok' : 'waiting']">
            {{ eventText(e.type) }}
          </span>
          <span v-if="e.iteration" class="muted small">第 {{ e.iteration }} 轮</span>
          <span v-if="e.tool" class="mono small">{{ e.tool }}</span>
          <span class="muted small">{{ e.at }}</span>
          <pre class="content">{{ e.content }}</pre>
        </div>
        <p v-if="!trace.events.length" class="muted small">该任务没有留下过程记录。</p>
      </div>
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

.small {
  font-size: 12px;
}

.head-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.head-row h3 {
  margin: 0;
  font-size: 14px;
}

.events {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 520px;
  overflow-y: auto;
}

.event {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel-2);
}

pre.content {
  flex-basis: 100%;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-dim);
  max-height: 260px;
  overflow-y: auto;
}
</style>
