<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { postJSON } from '../api'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

interface Message {
  role: 'user' | 'assistant'
  content: string
  at: string
}

interface Conversation {
  id: string
  name: string
  type: 'repo' | 'user'
  repo_full: string
  created_at: string
  updated_at: string
  messages: Message[]
}

interface Summary {
  id: string
  name: string
  type: 'repo' | 'user'
  repo_full: string
  created_at: string
  updated_at: string
  count: number
}

interface TaskRecord {
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

interface TraceItem {
  role: 'assistant' | 'tool' | 'note' | 'error'
  label: string
  content: string
}

interface RepoItem {
  rec: TaskRecord
  trace: Trace | null
  error: string
}

marked.setOptions({ gfm: true, breaks: true })

const conversations = ref<Summary[]>([])
const tasks = ref<TaskRecord[]>([])
const current = ref<Conversation | null>(null)
const activeRepo = ref('')
const repoItems = ref<RepoItem[]>([])
const repoConv = ref<Conversation | null>(null)
const input = ref('')
const sending = ref(false)
const err = ref('')
const scroller = ref<HTMLElement | null>(null)
let liveTimer: number | null = null
let listTimer: number | null = null

const EVENT_LABEL: Record<string, string> = {
  session: '复用会话',
  thinking: '思考',
  tool_call: '工具调用',
  tool_result: '工具结果',
  answer: '回答',
  note: '说明',
  error: '错误',
}

const STATE_TEXT: Record<string, string> = {
  wait: '等待下发',
  ready: '已下发',
  ok: '已完成',
  putout: '已放弃',
  failed: '失败',
}

/** 我的对话：没有绑定仓库的手动会话 */
const myConversations = computed(() => conversations.value.filter((c) => !c.repo_full))

/** 同一仓库的任务与追问合并为一个对话：按仓库分组，组内按时间正序 */
const repoGroups = computed(() => {
  const map = new Map<string, { tasks: TaskRecord[]; chatCount: number; chatAt: string }>()
  const touch = (repo: string) => {
    if (!map.has(repo)) map.set(repo, { tasks: [], chatCount: 0, chatAt: '' })
    return map.get(repo)!
  }
  for (const t of tasks.value) touch(t.repo_full).tasks.push(t)
  for (const c of conversations.value) {
    if (!c.repo_full) continue
    const entry = touch(c.repo_full)
    entry.chatCount = c.count
    entry.chatAt = c.updated_at || ''
  }
  return [...map.entries()]
    .map(([repo, entry]) => {
      const items = [...entry.tasks].sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''))
      const latest = items[items.length - 1]
      const latestAt = [latest?.created_at || '', entry.chatAt].sort().pop() || ''
      return { repo, items, chatCount: entry.chatCount, latest: latestAt, state: latest?.state || '' }
    })
    .sort((a, b) => b.latest.localeCompare(a.latest))
})

/** 仓库对话内容：任务过程与追问消息按时间穿插 */
interface RepoEntry {
  kind: 'task' | 'msg'
  key: string
  time: string
  task: RepoItem | null
  msg: Message | null
}

const repoEntries = computed<RepoEntry[]>(() => {
  const out: RepoEntry[] = []
  for (const it of repoItems.value) {
    out.push({ kind: 'task', key: `t-${it.rec.uuid}`, time: it.rec.created_at || '', task: it, msg: null })
  }
  for (const m of repoConv.value?.messages || []) {
    out.push({
      kind: 'msg',
      key: `m-${m.at}-${m.role}-${m.content.length}`,
      time: m.at || '',
      task: null,
      msg: m,
    })
  }
  // 旧的在上，新的排在下面（渲染时按时间正序）
  out.sort((a, b) => (a.time || '').localeCompare(b.time || ''))
  return out
})

function traceItems(trace: Trace | null): TraceItem[] {
  if (!trace) return []
  const out: TraceItem[] = []
  for (const e of trace.events) {
    const label = EVENT_LABEL[e.type] || e.type
    const suffix = e.iteration ? `（第 ${e.iteration} 轮）` : ''
    const content = e.content || ''
    if (e.type === 'tool_call') {
      out.push({
        role: 'tool',
        label: `工具调用 · ${e.tool || ''}${suffix}`,
        content: content.length > 1200 ? `${content.slice(0, 1200)}…` : content,
      })
    } else if (e.type === 'tool_result') {
      out.push({
        role: 'tool',
        label: `工具结果 · ${e.tool || ''}${suffix}`,
        content: content.length > 1200 ? `${content.slice(0, 1200)}…` : content,
      })
    } else if (e.type === 'error') {
      out.push({ role: 'error', label: '错误', content })
    } else if (e.type === 'answer' || e.type === 'thinking') {
      out.push({ role: 'assistant', label: `${label}${suffix}`, content })
    } else {
      out.push({ role: 'note', label: `${label}${suffix}`, content })
    }
  }
  return out
}

function renderMarkdown(text: string): string {
  const html = marked.parse(text || '', { async: false }) as string
  return DOMPurify.sanitize(html)
}

async function scrollToBottom() {
  await nextTick()
  const el = scroller.value
  if (el) el.scrollTop = el.scrollHeight
}

// ---------------------------------------------------------------- 数据加载
async function loadConversations() {
  try {
    const res = await postJSON<{ conversations: Summary[] }>('/api/chat/list', {})
    conversations.value = res.conversations || []
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

async function loadTasks() {
  try {
    const res = await postJSON<{ records: TaskRecord[] }>('/api/records', {})
    tasks.value = (res.records || []).filter((r) => r.uuid && r.repo_full)
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

async function openConv(id: string) {
  err.value = ''
  activeRepo.value = ''
  repoItems.value = []
  try {
    const res = await postJSON<{ conversation: Conversation }>('/api/chat/get', { id })
    current.value = res.conversation
    await scrollToBottom()
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

async function newConv() {
  err.value = ''
  activeRepo.value = ''
  repoItems.value = []
  repoConv.value = null
  try {
    const res = await postJSON<{ conversation: Conversation }>('/api/chat/create', {})
    current.value = res.conversation
    await loadConversations()
    await scrollToBottom()
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

async function removeConv(id: string, name: string) {
  if (!confirm(`确定删除对话「${name}」吗？`)) return
  try {
    await postJSON('/api/chat/remove', { id })
    if (current.value?.id === id) current.value = null
    await loadConversations()
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

/** 打开某个仓库的对话：把该仓库的全部任务按时间顺序拼成一段连续过程 */
async function openRepo(repo: string) {
  err.value = ''
  current.value = null
  activeRepo.value = repo
  const group = repoGroups.value.find((g) => g.repo === repo)
  repoItems.value = (group?.items || []).map((rec) => ({ rec, trace: null, error: '' }))
  repoConv.value = null
  try {
    // 该仓库的追问会话（没有则新建），用于在仓库对话里继续提问
    const res = await postJSON<{ conversation: Conversation }>('/api/chat/for-repo', { repo_full: repo })
    repoConv.value = res.conversation
  } catch (e: any) {
    err.value = `仓库对话不可用：${e?.message || e}`
  }
  for (const item of repoItems.value) {
    try {
      const res = await postJSON<{ trace: Trace }>('/api/Agent/trace', {
        repo_full: item.rec.repo_full,
        uuid: item.rec.uuid,
      })
      item.trace = res.trace
    } catch (e: any) {
      item.error = `过程记录不可用：${e?.message || e}`
    }
    await nextTick()
  }
  await scrollToBottom()
}

async function refreshAll() {
  if (!activeRepo.value) return
  for (const item of repoItems.value) {
    try {
      const res = await postJSON<{ trace: Trace }>('/api/Agent/trace', {
        repo_full: item.rec.repo_full,
        uuid: item.rec.uuid,
      })
      item.trace = res.trace
    } catch {
      /* 忽略单条刷新失败 */
    }
  }
}

/** 有任务在跑时，把最新轨迹贴到对应仓库对话里 */
async function pollLive() {
  try {
    const res = await postJSON<{ running: boolean; trace: Trace | null }>('/api/Agent/trace/live', {})
    if (!res.running || !res.trace) return
    if (res.trace.repo_full !== activeRepo.value) return
    const item = repoItems.value.find((it) => it.rec.uuid === res.trace!.uuid)
    if (item) {
      item.trace = res.trace
      await scrollToBottom()
    }
  } catch {
    /* 忽略 */
  }
}

// ---------------------------------------------------------------- 提问
async function send() {
  const text = input.value.trim()
  if (sending.value) return
  if (!text) {
    // 输入框看似有字却取不到内容，说明绑定异常，明确提示而不是静默无反应
    err.value = '请输入内容后再发送'
    return
  }
  err.value = ''
  if (activeRepo.value) {
    // 仓库对话：追问记在这个仓库的会话里
    input.value = ''
    sending.value = true
    try {
      await askRepo(activeRepo.value, text)
    } finally {
      sending.value = false
    }
    return
  }
  // 我的对话：提问必须带对话 ID，没有就先建一条（UUID）
  sending.value = true
  try {
    if (!current.value?.id) {
      const created = await postJSON<{ conversation: Conversation }>('/api/chat/create', {})
      current.value = created.conversation
      await loadConversations()
    }
    const convId = current.value.id
    current.value.messages.push({ role: 'user', content: text, at: '' })
    input.value = ''
    await scrollToBottom()
    const res = await postJSON<{ conversation: Conversation }>('/api/chat/ask', { id: convId, text })
    current.value = res.conversation
    await loadConversations()
  } catch (e: any) {
    err.value = e?.message || String(e)
    current.value?.messages.pop()
    input.value = text
  } finally {
    sending.value = false
    await scrollToBottom()
  }
}

function onKeydown(ev: KeyboardEvent) {
  if (ev.key === 'Enter' && !ev.shiftKey) {
    ev.preventDefault()
    send()
  }
}

/** 与后端一致的时间串（YYYY-MM-DD HH:MM:SS），用于乐观消息排序 */
function nowText(): string {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** 在「仓库对话」里追问：ID 由仓库名哈希得到，始终写进同一条会话 */
async function askRepo(repo: string, text: string) {
  // 先解析出该仓库的会话 ID（没有则由后端按哈希创建）
  if (!repoConv.value?.id) {
    try {
      const r = await postJSON<{ conversation: Conversation }>('/api/chat/for-repo', { repo_full: repo })
      repoConv.value = r.conversation
    } catch (e: any) {
      err.value = `仓库对话不可用：${e?.message || e}`
      return
    }
  }
  const conv = repoConv.value!
  const optimistic: Message = { role: 'user', content: text, at: nowText() }
  conv.messages.push(optimistic)
  await scrollToBottom()
  try {
    const res = await postJSON<{ conversation: Conversation }>('/api/chat/ask', { id: conv.id, text })
    repoConv.value = res.conversation
    await loadConversations()
  } catch (e: any) {
    err.value = e?.message || String(e)
    conv.messages = conv.messages.filter((m) => m !== optimistic)
    input.value = text
  } finally {
    await scrollToBottom()
  }
}

function stateClass(s: string): string {
  if (s === 'ok') return 'ok'
  if (s === 'failed') return 'failed'
  if (s === 'wait' || s === 'ready') return 'waiting'
  return ''
}

/** 任务是否在执行中（决定显示「正在执行」占位，还是「没有过程记录」） */
function isRunning(item: RepoItem | null): boolean {
  return item?.rec.state === 'ready' || item?.trace?.status === 'running'
}

onMounted(async () => {
  await Promise.all([loadConversations(), loadTasks()])
  if (conversations.value.length) await openConv(conversations.value[0].id)
  liveTimer = window.setInterval(pollLive, 3000)
  listTimer = window.setInterval(loadTasks, 15000)
})

onUnmounted(() => {
  if (liveTimer) clearInterval(liveTimer)
  if (listTimer) clearInterval(listTimer)
  liveTimer = listTimer = null
})
</script>

<template>
  <div class="chat">
    <aside class="sidebar panel">
      <div class="row head">
        <h3>AI 对话</h3>
        <div class="spacer"></div>
        <button class="primary" @click="newConv">新建</button>
      </div>

      <div class="list-wrap">
        <div class="group-title">我的对话</div>
        <ul v-if="myConversations.length" class="conv-list">
          <li
            v-for="c in myConversations"
            :key="c.id"
            :class="{ active: !activeRepo && current?.id === c.id }"
            @click="openConv(c.id)"
          >
            <div class="conv-main">
              <span class="conv-title">{{ c.name }}</span>
              <span class="muted small">{{ c.updated_at }} · {{ c.count }} 条</span>
            </div>
            <button class="ghost tiny" @click.stop="removeConv(c.id, c.name)">删除</button>
          </li>
        </ul>
        <p v-else class="muted small empty">还没有对话，点「新建」开始提问。</p>

        <div class="group-title">仓库对话</div>
        <ul v-if="repoGroups.length" class="conv-list">
          <li
            v-for="g in repoGroups"
            :key="g.repo"
            :class="{ active: activeRepo === g.repo }"
            @click="openRepo(g.repo)"
          >
            <div class="conv-main">
              <span class="conv-title mono">{{ g.repo }}</span>
              <span class="muted small">
                {{ g.latest }} · {{ g.items.length }} 个任务<template v-if="g.chatCount"> · {{ g.chatCount }} 条追问</template>
              </span>
            </div>
            <span :class="['status-tag', stateClass(g.state)]">{{ STATE_TEXT[g.state] || g.state }}</span>
          </li>
        </ul>
        <p v-else class="muted small empty">还没有任务记录。</p>
      </div>
    </aside>

    <section class="main">
      <div ref="scroller" class="messages">
        <div v-if="!current && !activeRepo" class="placeholder">
          <p class="muted">向 AI 提问，或从左侧选一个仓库查看它的全部任务过程。例如：</p>
          <ul class="muted small">
            <li>帮我写一个 GitHub Actions，在 issue 里出现关键词时发通知</li>
            <li>解释一下 CodeVoyage 的任务状态机是怎么流转的</li>
            <li>这段 Python 报 ProxyError，怎么排查？</li>
          </ul>
        </div>

        <!-- 仓库对话：同一仓库的多个任务合并成一段连续过程 -->
        <template v-if="activeRepo">
          <div class="row repo-bar">
            <span class="mono">{{ activeRepo }}</span>
            <span class="muted small">{{ repoItems.length }} 个任务</span>
            <div class="spacer"></div>
            <button class="ghost" @click="refreshAll">刷新</button>
          </div>
          <template v-for="entry in repoEntries" :key="entry.key">
            <div v-if="entry.kind === 'task'" class="task-block">
              <div class="task-head panel">
                <div class="row">
                  <span class="src-tag">来自 Issue #{{ entry.task?.rec.issue_number }}</span>
                  <span :class="['status-tag', stateClass(entry.task?.rec.state || '')]">
                    {{ STATE_TEXT[entry.task?.rec.state || ''] || entry.task?.rec.state }}
                  </span>
                  <span class="muted small">{{ entry.task?.rec.created_at }}</span>
                  <a v-if="entry.task?.rec.issue_url" :href="entry.task.rec.issue_url" target="_blank">Issue</a>
                  <a v-if="entry.task?.rec.pr_url" :href="entry.task.rec.pr_url" target="_blank">PR</a>
                  <div class="spacer"></div>
                  <span v-if="entry.task?.trace" class="muted small">
                    {{ entry.task.trace.events.length }} 条事件
                  </span>
                </div>
                <p v-if="entry.task?.rec.title" class="muted small">{{ entry.task.rec.title }}</p>
              </div>
              <div v-if="isRunning(entry.task)" class="msg assistant">
                <div class="who">状态</div>
                <div class="bubble md muted">正在执行…</div>
              </div>
              <p v-if="entry.task?.error" class="alert">{{ entry.task.error }}</p>
              <div v-for="(m, i) in traceItems(entry.task?.trace || null)" :key="i" :class="['msg', m.role]">
                <div class="who">任务 · {{ m.label }}</div>
                <div v-if="m.role === 'assistant'" class="bubble md" v-html="renderMarkdown(m.content)"></div>
                <div v-else-if="m.role === 'tool'" class="bubble tool mono">{{ m.content }}</div>
                <div v-else class="bubble note">{{ m.content }}</div>
              </div>
              <p
                v-if="!isRunning(entry.task) && (!entry.task?.trace || !entry.task.trace.events.length)"
                class="muted small"
              >
                该任务没有留下过程记录。
              </p>
            </div>
            <div v-else :class="['msg', entry.msg?.role || 'user']">
              <div class="who">{{ entry.msg?.role === 'user' ? '你' : 'AI 助手' }}</div>
              <div
                v-if="entry.msg?.role === 'assistant'"
                class="bubble md"
                v-html="renderMarkdown(entry.msg?.content || '')"
              ></div>
              <div v-else class="bubble plain">{{ entry.msg?.content }}</div>
            </div>
          </template>
        </template>

        <!-- 我的对话 -->
        <template v-else>
          <div v-for="(m, i) in current?.messages || []" :key="i" :class="['msg', m.role]">
            <div class="who">{{ m.role === 'user' ? '你' : 'AI' }}</div>
            <div v-if="m.role === 'assistant'" class="bubble md" v-html="renderMarkdown(m.content)"></div>
            <div v-else class="bubble plain">{{ m.content }}</div>
          </div>
        </template>

        <div v-if="sending" class="msg assistant">
          <div class="who">AI</div>
          <div class="bubble md muted">正在思考…</div>
        </div>
      </div>

      <div v-if="err" class="alert banner">{{ err }}</div>

      <div class="composer panel">
        <textarea
          v-model="input"
          rows="3"
          :placeholder="
            activeRepo
              ? `针对 ${activeRepo} 提问，Enter 发送，Shift+Enter 换行`
              : '输入问题，Enter 发送，Shift+Enter 换行'
          "
          @keydown="onKeydown"
        ></textarea>
        <div class="row">
          <span class="muted small">
            <template v-if="activeRepo">提问会记入「{{ activeRepo }}」的仓库对话</template>
            <template v-else>使用「配置」页中的 LLM 配置（多条按顺序回退）</template>
          </span>
          <div class="spacer"></div>
          <button class="primary" :disabled="sending" @click="send">
            {{ sending ? '发送中…' : '发送' }}
          </button>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.chat {
  display: flex;
  gap: 24px;
  height: calc(100vh - 122px);
}

.sidebar {
  width: 280px;
  flex: 0 0 280px;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.head {
  align-items: center;
  margin-bottom: 10px;
}

.head h3 {
  margin: 0;
  font-size: 14px;
}

.list-wrap {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.group-title {
  font-size: 12px;
  color: var(--text-dim);
  margin: 10px 0 6px;
}

.group-title:first-child {
  margin-top: 0;
}

.conv-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.conv-list li {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: 8px;
  cursor: pointer;
}

.conv-list li:hover {
  background: var(--panel-2);
}

.conv-list li.active {
  border-color: var(--accent);
  background: var(--panel-2);
}

.conv-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.conv-title {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

button.tiny {
  font-size: 12px;
  padding: 2px 8px;
}

.empty {
  margin: 4px 0 0;
}

.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding-right: 4px;
}

.placeholder ul {
  margin: 8px 0 0 18px;
  line-height: 1.9;
}

.repo-bar {
  align-items: center;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}

/* 来源标记：区分「来自 Issue」与用户 / AI 的消息 */
.src-tag {
  font-size: 12px;
  padding: 1px 8px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--text-dim);
  white-space: nowrap;
}

/* 同一仓库的每个任务：一段连续的对话块 */
.task-block {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-left: 12px;
  border-left: 2px solid var(--border);
}

.task-head {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.msg {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-width: 100%;
}

.msg.user {
  align-items: flex-end;
}

.who {
  font-size: 12px;
  color: var(--text-dim);
}

.bubble {
  padding: 10px 14px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--panel);
  max-width: 86%;
  overflow-wrap: anywhere;
}

.msg.user .bubble {
  background: var(--panel-2);
  border-color: var(--accent);
}

.bubble.plain {
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.7;
}

/* 工具调用 / 结果：紧凑等宽块 */
.bubble.tool {
  max-width: 100%;
  background: var(--panel-2);
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  max-height: 260px;
  overflow-y: auto;
}

.bubble.note {
  border-style: dashed;
  color: var(--text-dim);
  font-size: 12px;
}

.msg.error .bubble {
  border-color: var(--danger, #b4443c);
  color: var(--danger, #b4443c);
}

.small {
  font-size: 12px;
}

/* 错误提示固定在输入区上方，不会被消息区滚动带出视野 */
.alert.banner {
  flex: 0 0 auto;
}

.composer {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.composer textarea {
  width: 100%;
  resize: vertical;
  min-height: 64px;
}

/* Markdown 渲染样式（v-html 内容不受 scoped 限制，用 :deep 穿透） */
.md :deep(h1),
.md :deep(h2),
.md :deep(h3),
.md :deep(h4) {
  margin: 14px 0 8px;
  font-size: 15px;
  line-height: 1.4;
}

.md :deep(h1:first-child),
.md :deep(h2:first-child),
.md :deep(p:first-child) {
  margin-top: 0;
}

.md :deep(p) {
  margin: 8px 0;
  font-size: 13px;
  line-height: 1.75;
}

.md :deep(ul),
.md :deep(ol) {
  margin: 8px 0 8px 20px;
  font-size: 13px;
  line-height: 1.8;
}

.md :deep(code) {
  font-family: Consolas, "Courier New", monospace;
  font-size: 12px;
  background: var(--panel-2);
  padding: 1px 5px;
  border-radius: 4px;
}

.md :deep(pre) {
  margin: 10px 0;
  padding: 10px 12px;
  background: #0b0d11;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow-x: auto;
}

.md :deep(pre code) {
  background: transparent;
  padding: 0;
  line-height: 1.6;
}

.md :deep(blockquote) {
  margin: 8px 0;
  padding: 4px 12px;
  border-left: 3px solid var(--border);
  color: var(--text-dim);
  font-size: 13px;
}

.md :deep(table) {
  border-collapse: collapse;
  margin: 10px 0;
  font-size: 13px;
  width: 100%;
}

.md :deep(th),
.md :deep(td) {
  border: 1px solid var(--border);
  padding: 6px 10px;
  text-align: left;
}

.md :deep(th) {
  background: var(--panel-2);
}

.md :deep(a) {
  color: var(--accent);
}

.md :deep(hr) {
  border: none;
  border-top: 1px solid var(--border);
  margin: 14px 0;
}
</style>
