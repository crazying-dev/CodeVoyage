<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { postJSON, fetchWorkflow, downloadText } from '../api'

interface Cred {
  id: number
  kind: string
  label: string
  name: string
  masked: string
  position: number
  base_url?: string
  model?: string
  repo?: string
}

interface Repo {
  id: number
  owner: string
  name: string
  keywords: string[]
  authors: string[]
  issue_count: number
  created_at: string
  has_repo_token: boolean
  repo_token_hints: string[]
  token_source: 'repo' | 'global' | ''
  token_count: number
}

interface PrState {
  running: boolean
  ok: boolean
  pr_url: string
  branch: string
  reason: string
  steps: string[]
}

const err = ref('')
const msg = ref('')
const tokens = ref<Cred[]>([])
const llms = ref<Cred[]>([])
const repos = ref<Repo[]>([])
const cachedInfo = ref<Record<string, number>>({})

const adding = ref<'' | 'token' | 'llm' | 'repo'>('')
const tokenForm = ref({ value: '', name: '' })
const llmForm = ref({ api_key: '', base_url: '', model: '', name: '' })
const repoForm = ref({ owner: '', name: '', keywords: '', authors: '', token: '', autoPr: true })

const wfStatus = ref<Record<number, 'unknown' | 'checking' | 'installed' | 'outdated' | 'missing' | 'pending'>>({})
const WF_TEXT: Record<string, string> = {
  unknown: '未检查',
  checking: '检查中…',
  installed: 'workflow 已装',
  outdated: 'workflow 旧版',
  missing: 'workflow 未装',
  pending: 'PR 待合并',
}

const prMap = ref<Record<number, PrState>>({})
const editRepo = ref<Repo | null>(null)
const editForm = ref({ keywords: '', authors: '', token: '' })

const gitName = ref('')
const gitEmail = ref('')
const savingId = ref(0)

interface ProxyState {
  proxy_url: string
  active: boolean
  mode: string
  local: Record<string, any>
  nodes: { node_id: string; email: string; port: number; github_ok: boolean; mine: boolean }[]
  stats: Record<string, any>
  error: string
}

const proxy = ref<ProxyState | null>(null)
const proxyUse = ref(true)
const proxyHelper = ref(true)
const proxyBusy = ref('')
const proxyTest = ref<{ ok: boolean; via?: string; seconds?: number; reason?: string } | null>(null)

async function loadProxy() {
  proxyBusy.value = 'load'
  try {
    const st = await postJSON<ProxyState>('/api/proxy/status', {})
    proxy.value = st
    proxyUse.value = !!st.local?.enabled
    proxyHelper.value = !!st.local?.as_helper
  } catch (e: any) {
    err.value = e?.message || String(e)
  } finally {
    proxyBusy.value = ''
  }
}

async function saveProxy() {
  try {
    const st = await postJSON<ProxyState>('/api/proxy/status', {
      enabled: proxyUse.value,
      as_helper: proxyHelper.value,
    })
    proxy.value = st
    msg.value = '代理设置已更新'
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

async function testProxy() {
  proxyBusy.value = 'test'
  proxyTest.value = null
  try {
    proxyTest.value = await postJSON('/api/proxy/test', {})
  } catch (e: any) {
    err.value = e?.message || String(e)
  } finally {
    proxyBusy.value = ''
  }
}

const menu = ref<{ show: boolean; x: number; y: number; items: { label: string; run: () => void }[] }>({
  show: false, x: 0, y: 0, items: [],
})

function closeMenu() {
  menu.value.show = false
}

function openMenu(ev: MouseEvent, items: { label: string; run: () => void }[]) {
  ev.preventDefault()
  menu.value = {
    show: true,
    x: Math.min(ev.clientX, window.innerWidth - 180),
    y: Math.min(ev.clientY, window.innerHeight - 20 - items.length * 30),
    items: items.map((it) => ({
      label: it.label,
      run: () => {
        menu.value.show = false
        it.run()
      },
    })),
  }
}

function splitList(text: string, stripAt = false): string[] {
  return (text || '')
    .split(/[,，\n]/)
    .map((k) => {
      const item = k.trim()
      return stripAt ? item.replace(/^@/, '') : item
    })
    .filter(Boolean)
}

function wfClass(id: number): string {
  const s = wfStatus.value[id] || 'unknown'
  if (s === 'installed') return 'ok'
  if (s === 'outdated' || s === 'pending') return 'waiting'
  if (s === 'missing') return 'failed'
  if (s === 'checking') return 'running'
  return ''
}

async function refresh() {
  err.value = ''
  try {
    const cred = await postJSON<{
      github_tokens: Cred[]
      llm: Cred[]
      cached: Record<string, number>
      error?: string
    }>('/api/local/credentials', {})
    tokens.value = cred.github_tokens || []
    llms.value = cred.llm || []
    cachedInfo.value = cred.cached || {}
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
  try {
    const j = await postJSON<{ repos: Repo[] }>('/api/repo/list', {})
    repos.value = j.repos
    refreshWorkflowStatuses()
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

async function refreshStatus() {
  try {
    const s = await postJSON<{ git_name: string; git_email: string }>('/api/local/config', {})
    gitName.value = s.git_name || ''
    gitEmail.value = s.git_email || ''
  } catch {
    /* 忽略 */
  }
}

async function refreshWorkflowStatuses() {
  if (!repos.value.length) return
  const next: Record<number, any> = {}
  repos.value.forEach((r) => (next[r.id] = 'checking'))
  wfStatus.value = next
  const results = await Promise.all(
    repos.value.map(async (r) => {
      try {
        const res = await postJSON<{ installed: boolean; up_to_date: boolean }>(
          '/api/repo/check-workflow',
          { owner: r.owner, name: r.name },
        )
        return { id: r.id, status: res.installed ? (res.up_to_date ? 'installed' : 'outdated') : 'missing' }
      } catch {
        return { id: r.id, status: 'unknown' as const }
      }
    }),
  )
  const updated = { ...wfStatus.value }
  results.forEach((res) => (updated[res.id] = res.status))
  wfStatus.value = updated
}

/* ---------------- GitHub Token / LLM ---------------- */
async function submitToken() {
  if (!tokenForm.value.value.trim()) {
    err.value = '请填写令牌'
    return
  }
  try {
    await postJSON('/api/local/tokens', {
      action: 'add', token: tokenForm.value.value.trim(), name: tokenForm.value.name.trim(),
    })
    tokenForm.value = { value: '', name: '' }
    adding.value = ''
    msg.value = '令牌已保存到服务端'
    await refresh()
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

async function submitLlm() {
  if (!llmForm.value.api_key.trim()) {
    err.value = '请填写 API Key'
    return
  }
  try {
    await postJSON('/api/local/llm', { action: 'add', ...llmForm.value })
    llmForm.value = { api_key: '', base_url: '', model: '', name: '' }
    adding.value = ''
    msg.value = 'LLM 配置已保存到服务端'
    await refresh()
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

async function moveCred(kind: 'token' | 'llm', id: number, delta: number) {
  savingId.value = id
  try {
    const url = kind === 'token' ? '/api/local/tokens' : '/api/local/llm'
    await postJSON(url, { action: 'move', id, delta })
    await refresh()
  } catch (e: any) {
    err.value = e?.message || String(e)
  } finally {
    savingId.value = 0
  }
}

async function removeCred(kind: 'token' | 'llm', id: number, masked: string) {
  if (!confirm(`确定删除 ${masked} 吗？`)) return
  try {
    const url = kind === 'token' ? '/api/local/tokens' : '/api/local/llm'
    await postJSON(url, { action: 'remove', id })
    msg.value = '已删除'
    await refresh()
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

/* ---------------- 仓库 ---------------- */
async function submitRepo() {
  const o = repoForm.value.owner.trim()
  const n = repoForm.value.name.trim()
  if (!o || !n) {
    err.value = '请填写仓库作者与名称'
    return
  }
  try {
    const res = await postJSON<{ id: number }>('/api/repo/bind', {
      owner: o, name: n,
      keywords: splitList(repoForm.value.keywords),
      authors: splitList(repoForm.value.authors, true),
    })
    if (repoForm.value.token.trim()) {
      await postJSON('/api/local/repo-token', { owner: o, name: n, token: repoForm.value.token.trim() })
    }
    const auto = repoForm.value.autoPr
    repoForm.value = { owner: '', name: '', keywords: '', authors: '', token: '', autoPr: true }
    adding.value = ''
    await refresh()
    if (auto && res.id) {
      msg.value = '绑定成功，正在发送 workflow PR…'
      await sendPr({ id: res.id, owner: o, name: n })
    } else {
      msg.value = '绑定成功'
    }
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

function startEdit(r: Repo) {
  editRepo.value = r
  editForm.value = { keywords: (r.keywords || []).join('，'), authors: (r.authors || []).join('，'), token: '' }
}

async function saveEdit() {
  const r = editRepo.value
  if (!r) return
  savingId.value = r.id
  try {
    await postJSON('/api/repo/update', {
      id: r.id,
      keywords: splitList(editForm.value.keywords),
      authors: splitList(editForm.value.authors, true),
    })
    if (editForm.value.token.trim()) {
      await postJSON('/api/local/repo-token', { owner: r.owner, name: r.name, token: editForm.value.token.trim() })
    }
    editRepo.value = null
    await refresh()
    msg.value = '已保存；如需让关键词生效，请「提交 workflow PR」'
  } catch (e: any) {
    err.value = e?.message || String(e)
  } finally {
    savingId.value = 0
  }
}

async function unbind(r: Repo) {
  if (!confirm(`确定解绑 ${r.owner}/${r.name} 吗？`)) return
  try {
    await postJSON('/api/repo/unbind', { id: r.id })
    await postJSON('/api/local/repo-token', { owner: r.owner, name: r.name, action: 'clear' })
    delete prMap.value[r.id]
    msg.value = '已解绑'
    await refresh()
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

async function downloadWorkflow(r: Repo) {
  try {
    const wf = await fetchWorkflow(r.id)
    downloadText(wf.filename, wf.text, 'text/yaml')
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

/** 提交 workflow PR：准备特性分支 → 提交 → 推送 → 调接口建 PR */
async function sendPr(target: { id: number; owner: string; name: string }) {
  prMap.value[target.id] = { running: true, ok: false, pr_url: '', branch: '', reason: '', steps: [] }
  try {
    const res = await postJSON<{
      ok: boolean
      reason?: string
      pr_url?: string
      branch?: string
      steps?: string[]
      tried?: number
      author_email?: string
      unchanged?: boolean
    }>('/api/repo/install-workflow', { id: target.id, owner: target.owner, name: target.name })
    if (res.ok && res.unchanged) {
      prMap.value[target.id] = {
        running: false, ok: true, pr_url: '', branch: res.branch || '',
        reason: res.reason || '仓库中的 workflow 已是最新', steps: res.steps || [],
      }
      wfStatus.value[target.id] = 'installed'
      msg.value = 'workflow 已是最新，无需重复提交'
    } else if (res.ok) {
      prMap.value[target.id] = {
        running: false, ok: true, pr_url: res.pr_url || '', branch: res.branch || '',
        reason: `提交邮箱 ${res.author_email || ''}（用了第 ${res.tried || 1} 个令牌）`,
        steps: res.steps || [],
      }
      wfStatus.value[target.id] = 'pending'
      msg.value = 'PR 已创建，请前往 GitHub 合并'
    } else {
      prMap.value[target.id] = {
        running: false, ok: false, pr_url: '', branch: '',
        reason: res.reason || '提交失败', steps: res.steps || [],
      }
    }
  } catch (e: any) {
    prMap.value[target.id] = {
      running: false, ok: false, pr_url: '', branch: '', reason: e?.message || String(e), steps: [],
    }
  }
}

async function saveIdentity() {
  try {
    await postJSON('/api/local/config', { git_name: gitName.value, git_email: gitEmail.value })
    msg.value = '提交身份已保存'
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

onMounted(() => {
  document.addEventListener('click', closeMenu)
  refresh()
  refreshStatus()
  loadProxy()
})

onUnmounted(() => {
  document.removeEventListener('click', closeMenu)
  closeMenu()
})
</script>

<template>
  <div class="config">
    <div v-if="err" class="alert">{{ err }}</div>
    <div v-if="msg" class="ok">{{ msg }}</div>

    <div class="board">
      <!-- GitHub Token -->
      <section class="panel col">
        <header class="col-head">
          <h3>GitHub Token</h3>
          <button class="ghost" @click="adding = adding === 'token' ? '' : 'token'">+ add</button>
        </header>
        <form v-if="adding === 'token'" class="add-form" @submit.prevent="submitToken">
          <input v-model="tokenForm.value" placeholder="令牌（ghp_… 或 github_pat_…）" />
          <input v-model="tokenForm.name" placeholder="备注（可选）" />
          <div class="row">
            <button class="primary" type="submit">保存</button>
            <button type="button" @click="adding = ''">取消</button>
          </div>
        </form>
        <ul v-if="tokens.length" class="items">
          <li
            v-for="t in tokens"
            :key="t.id"
            @contextmenu="openMenu($event, [
              { label: '上移', run: () => moveCred('token', t.id, -1) },
              { label: '下移', run: () => moveCred('token', t.id, 1) },
              { label: '删除', run: () => removeCred('token', t.id, t.masked) },
            ])"
          >
            <span class="mono">{{ t.masked }}</span>
            <span class="muted small">{{ t.name || '（无备注）' }}</span>
          </li>
        </ul>
        <p v-else class="muted small">未配置。点右上「+ add」添加；顺序即回退顺序。</p>
      </section>

      <!-- LLM API -->
      <section class="panel col">
        <header class="col-head">
          <h3>LLM API</h3>
          <button class="ghost" @click="adding = adding === 'llm' ? '' : 'llm'">+ add</button>
        </header>
        <form v-if="adding === 'llm'" class="add-form" @submit.prevent="submitLlm">
          <input v-model="llmForm.api_key" placeholder="API Key（必填）" />
          <input v-model="llmForm.base_url" placeholder="Base URL（可选，如 https://api.openai.com/v1）" />
          <input v-model="llmForm.model" placeholder="模型（可选）" />
          <input v-model="llmForm.name" placeholder="备注（可选）" />
          <div class="row">
            <button class="primary" type="submit">保存</button>
            <button type="button" @click="adding = ''">取消</button>
          </div>
        </form>
        <ul v-if="llms.length" class="items">
          <li
            v-for="c in llms"
            :key="c.id"
            @contextmenu="openMenu($event, [
              { label: '上移', run: () => moveCred('llm', c.id, -1) },
              { label: '下移', run: () => moveCred('llm', c.id, 1) },
              { label: '删除', run: () => removeCred('llm', c.id, c.masked) },
            ])"
          >
            <span class="mono">{{ c.masked }}</span>
            <span class="muted small">{{ c.name || c.model || '（无备注）' }}{{ c.model ? ` · ${c.model}` : '' }}</span>
          </li>
        </ul>
        <p v-else class="muted small">未配置。顺序即回退顺序：前一个调用失败自动换下一个。</p>
      </section>

      <!-- 仓库 -->
      <section class="panel col">
        <header class="col-head">
          <h3>仓库</h3>
          <button class="ghost" @click="adding = adding === 'repo' ? '' : 'repo'">+ add</button>
        </header>
        <form v-if="adding === 'repo'" class="add-form" @submit.prevent="submitRepo">
          <input v-model="repoForm.owner" placeholder="仓库作者（owner）" />
          <input v-model="repoForm.name" placeholder="仓库名（name）" />
          <input v-model="repoForm.keywords" placeholder="触发关键词，逗号分隔" />
          <input v-model="repoForm.authors" placeholder="允许触发的用户，逗号分隔（留空=仅 owner）" />
          <input v-model="repoForm.token" placeholder="该仓库的细粒度令牌（可选）" />
          <label class="check"><input type="checkbox" v-model="repoForm.autoPr" /> 绑定后自动提交 workflow PR</label>
          <div class="row">
            <button class="primary" type="submit">绑定</button>
            <button type="button" @click="adding = ''">取消</button>
          </div>
        </form>
        <ul v-if="repos.length" class="items">
          <li
            v-for="r in repos"
            :key="r.id"
            @contextmenu="openMenu($event, [
              { label: '下载 workflow 文件', run: () => downloadWorkflow(r) },
              { label: '提交 workflow PR', run: () => sendPr({ id: r.id, owner: r.owner, name: r.name }) },
              { label: '编辑', run: () => startEdit(r) },
              { label: '解绑', run: () => unbind(r) },
            ])"
          >
            <span class="mono">{{ r.owner }}/{{ r.name }}</span>
            <span :class="['status-tag', wfClass(r.id)]">{{ WF_TEXT[wfStatus[r.id] || 'unknown'] }}</span>
          </li>
        </ul>
        <p v-else class="muted small">未绑定仓库。点右上「+ add」绑定。</p>
      </section>
    </div>

    <div class="panel identity">
      <h3>提交身份</h3>
      <div class="row wrap">
        <label class="muted small">用户名</label>
        <input class="narrow" v-model="gitName" placeholder="CodeVoyage AI" />
        <label class="muted small">邮箱</label>
        <input class="narrow" v-model="gitEmail" placeholder="user@example.com" />
        <button class="primary" @click="saveIdentity">保存</button>
        <span class="muted small">用于 git 提交与 PR 的作者信息</span>
      </div>
    </div>

    <div class="panel">
      <div class="row head-row">
        <h3>GitHub 代理（客户端互助）</h3>
        <div class="spacer"></div>
        <button :disabled="!!proxyBusy" @click="loadProxy">刷新</button>
        <button :disabled="!!proxyBusy" @click="testProxy">
          {{ proxyBusy === 'test' ? '测试中…' : '连接自检' }}
        </button>
      </div>
      <p class="hint">
        直连 GitHub 超时时，自动经由其它可连的客户端代连（先打洞、失败走服务端中继）。
        代连方只做 TLS 盲转发，看不到你的令牌与代码。默认开启，可随时关闭。详见
        <RouterLink to="/agreement" target="_blank">《用户协议》</RouterLink>。
      </p>
      <div class="row wrap">
        <label class="check">
          <input type="checkbox" v-model="proxyUse" @change="saveProxy" />
          使用代理（借用他人网络访问 GitHub）
        </label>
        <label class="check">
          <input type="checkbox" v-model="proxyHelper" @change="saveProxy" />
          作为代连节点（为他人转发 GitHub 流量）
        </label>
      </div>
      <p v-if="proxy" class="hint">
        本地代理：<span class="mono">{{ proxy.proxy_url }}</span>
        （{{ proxy.active ? '已就绪' : '未启用' }}） ·
        模式：{{ proxy.mode === 'proxy' ? '走代理' : '直连' }} ·
        在线代连节点：{{ (proxy.nodes || []).length }}
      </p>
      <p v-if="proxyTest" class="hint">
        连接自检：
        <span v-if="proxyTest.ok" class="status-tag ok">
          成功（{{ proxyTest.via === 'direct' ? '打洞直连' : '服务端中继' }}，{{ proxyTest.seconds }}s）
        </span>
        <span v-else class="status-tag failed">失败：{{ proxyTest.reason }}</span>
      </p>
      <ul v-if="proxy" class="hint-list">
        <li>
          借用 {{ proxy.local?.borrow_count || 0 }} 次（打洞成功 {{ proxy.local?.direct_ok || 0 }} /
          中继成功 {{ proxy.local?.relay_ok || 0 }} / 中继失败 {{ proxy.local?.relay_fails || 0 }}）
        </li>
        <li>代连 {{ proxy.local?.assist_count || 0 }} 次</li>
        <li v-if="proxy.local?.last_event">最近：{{ proxy.local.last_event }}</li>
        <li v-if="proxy.error">服务端协调不可用：{{ proxy.error }}</li>
      </ul>
    </div>

    <div v-if="editRepo" class="panel">
      <div class="row head-row">
        <h3>编辑 {{ editRepo.owner }}/{{ editRepo.name }}</h3>
        <div class="spacer"></div>
        <button @click="editRepo = null">关闭</button>
      </div>
      <div class="row wrap">
        <label class="muted small">关键词</label>
        <input v-model="editForm.keywords" placeholder="逗号分隔" />
      </div>
      <div class="row wrap">
        <label class="muted small">允许触发</label>
        <input v-model="editForm.authors" placeholder="逗号分隔，留空=仅 owner" />
      </div>
      <div class="row wrap">
        <label class="muted small">追加令牌</label>
        <input v-model="editForm.token" placeholder="细粒度令牌（可选）" />
      </div>
      <div class="row">
        <button class="primary" :disabled="savingId === editRepo.id" @click="saveEdit">保存</button>
        <span class="muted small">
          该仓库现有专属令牌 {{ (editRepo.repo_token_hints || []).length }} 个
        </span>
      </div>
    </div>

    <div v-if="Object.keys(prMap).length" class="panel">
      <h3>workflow PR 状态</h3>
      <div v-for="(st, id) in prMap" :key="id" class="pr-block">
        <div class="row">
          <span :class="['status-tag', st.running ? 'running' : st.ok ? 'ok' : 'failed']">
            {{ st.running ? '执行中' : st.ok ? '成功' : '失败' }}
          </span>
          <a v-if="st.pr_url" :href="st.pr_url" target="_blank">{{ st.pr_url }}</a>
          <span class="muted small">{{ st.reason }}</span>
        </div>
        <ol v-if="st.steps.length" class="steps">
          <li v-for="(s, i) in st.steps" :key="i">{{ s }}</li>
        </ol>
      </div>
    </div>

    <div v-if="menu.show" class="ctx" :style="{ left: menu.x + 'px', top: menu.y + 'px' }">
      <button v-for="it in menu.items" :key="it.label" @click="it.run()">{{ it.label }}</button>
    </div>
  </div>
</template>

<style scoped>
.board {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  align-items: start;
}

@media (max-width: 1100px) {
  .board {
    grid-template-columns: 1fr;
  }
}

.col {
  min-height: 180px;
}

.col-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.col-head h3 {
  margin: 0;
  font-size: 14px;
}

.add-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 12px;
}

.add-form input {
  width: 100%;
}

.items {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.items li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel-2);
  cursor: default;
}

.items li:hover {
  border-color: var(--accent);
}

.small {
  font-size: 12px;
}

.identity,
.pr-block {
  margin-top: 14px;
}

.identity h3,
.panel h3 {
  margin: 0 0 10px;
  font-size: 14px;
}

.wrap {
  flex-wrap: wrap;
}

input.narrow {
  width: 200px;
}

.check {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text-dim);
}

.hint-list {
  margin: 6px 0 0 18px;
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.8;
}

.steps {
  margin: 8px 0 0 18px;
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.7;
}

.ctx {
  position: fixed;
  z-index: 50;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 4px;
  display: flex;
  flex-direction: column;
  min-width: 150px;
  box-shadow: 0 8px 24px rgb(0 0 0 / 0.35);
}

.ctx button {
  background: transparent;
  border-color: transparent;
  text-align: left;
  padding: 6px 10px;
  font-size: 13px;
}

.ctx button:hover {
  background: var(--panel-2);
}
</style>
