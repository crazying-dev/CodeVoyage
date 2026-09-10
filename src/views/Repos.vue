<script setup lang="ts">
import { onActivated, ref } from 'vue'
import { postJSON, fetchWorkflow, downloadText } from '../api'

interface Repo {
  id: number
  owner: string
  name: string
  keywords: string[]
  authors: string[]
  issue_count: number
  created_at: string
  has_repo_token: boolean
  repo_token_hint: string
  repo_token_hints: string[]
  token_source: 'repo' | 'global' | ''
  token_count: number
  global_token_count: number
}

interface PrState {
  running: boolean
  ok: boolean
  pr_url: string
  branch: string
  reason: string
  steps: string[]
}

const repos = ref<Repo[]>([])
const err = ref('')
const msg = ref('')

const owner = ref('')
const name = ref('')
const keywords = ref('')
const authors = ref('')
const repoToken = ref('')
const autoPr = ref(true)

// 行内编辑：关键词 + 白名单 + 追加令牌
const editMap = ref<Record<number, { keywords: string; authors: string; token: string }>>({})
const busyId = ref<number | null>(null)

const loading = ref(false)
const checkingWf = ref(false)
const wfStatus = ref<Record<number, 'unknown' | 'checking' | 'installed' | 'outdated' | 'missing'>>({})
const wfReason = ref('')

// 每个仓库的 PR 发送状态
const prMap = ref<Record<number, PrState>>({})
const fallback = ref<{ repo: string; path: string; content: string; new_file_urls: string[]; repo_url: string } | null>(null)

const WF_TEXT: Record<string, string> = {
  unknown: '未检查',
  checking: '检查中…',
  installed: 'workflow 已装',
  outdated: 'workflow 旧版',
  missing: 'workflow 未装',
}

const TOKEN_TEXT: Record<string, string> = {
  repo: '仓库专属令牌',
  global: '全局传统令牌',
  '': '无令牌',
}

function wfClass(id: number): string {
  const s = wfStatus.value[id] || 'unknown'
  if (s === 'installed') return 'ok'
  if (s === 'outdated') return 'waiting'
  if (s === 'missing') return 'failed'
  if (s === 'checking') return 'running'
  return ''
}

function tokenClass(r: Repo): string {
  if (r.token_source === 'repo') return 'ok'
  if (r.token_source === 'global') return 'waiting'
  return 'failed'
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

async function refresh() {
  err.value = ''
  loading.value = true
  try {
    const j = await postJSON<{ repos: Repo[] }>('/api/repo/list', {})
    repos.value = j.repos
    loading.value = false
    refreshWorkflowStatuses()
  } catch (e: any) {
    err.value = e?.message || String(e)
    loading.value = false
  }
}

async function refreshWorkflowStatuses() {
  if (!repos.value.length) return
  checkingWf.value = true
  wfReason.value = ''
  const next: Record<number, 'unknown' | 'checking' | 'installed' | 'outdated' | 'missing'> = {}
  repos.value.forEach((r) => (next[r.id] = 'checking'))
  wfStatus.value = next

  const results = await Promise.all(
    repos.value.map(async (r) => {
      try {
        const res = await postJSON<{ installed: boolean; up_to_date: boolean; reason: string }>(
          '/api/repo/check-workflow',
          { owner: r.owner, name: r.name },
        )
        return {
          id: r.id,
          status: res.installed ? (res.up_to_date ? 'installed' : 'outdated') : 'missing',
          reason: res.reason || '',
        }
      } catch (e: any) {
        return { id: r.id, status: 'unknown' as const, reason: e?.message || String(e) }
      }
    }),
  )
  const updated = { ...wfStatus.value }
  results.forEach((res) => {
    updated[res.id] = res.status as any
    if (res.reason && !wfReason.value) wfReason.value = res.reason
  })
  wfStatus.value = updated
  checkingWf.value = false
}

/** 发送/重发 PR：准备特性分支 → 提交 → 推送 → 调接口建 PR */
async function sendPr(target: { id: number; owner: string; name: string }) {
  prMap.value[target.id] = { running: true, ok: false, pr_url: '', branch: '', reason: '', steps: [] }
  fallback.value = null
  try {
    const res = await postJSON<{
      ok: boolean
      reason?: string
      pr_url?: string
      branch?: string
      steps?: string[]
      token_source?: string
      tried?: number
      author_email?: string
      fallback?: { filename: string; path: string; content: string; repo_url: string; new_file_urls: string[] }
    }>('/api/repo/install-workflow', { id: target.id, owner: target.owner, name: target.name })

    if (res.ok) {
      prMap.value[target.id] = {
        running: false, ok: true, pr_url: res.pr_url || '', branch: res.branch || '',
        reason: `提交邮箱 ${res.author_email || ''}（用了第 ${res.tried || 1} 个令牌）`,
        steps: res.steps || [],
      }
      wfStatus.value[target.id] = 'pending'
      msg.value = 'PR 已创建，请前往 GitHub 合并'
    } else {
      prMap.value[target.id] = {
        running: false, ok: false, pr_url: '', branch: '', reason: res.reason || '提交失败', steps: res.steps || [],
      }
      if (res.fallback) {
        fallback.value = {
          repo: `${target.owner}/${target.name}`,
          path: res.fallback.path,
          content: res.fallback.content,
          new_file_urls: res.fallback.new_file_urls,
          repo_url: res.fallback.repo_url,
        }
      }
    }
  } catch (e: any) {
    prMap.value[target.id] = {
      running: false, ok: false, pr_url: '', branch: '', reason: e?.message || String(e), steps: [],
    }
  }
}

async function bind() {
  err.value = ''
  msg.value = ''
  const o = owner.value.trim()
  const n = name.value.trim()
  if (!o || !n) {
    err.value = '请填写仓库作者与名称'
    return
  }
  try {
    const res = await postJSON<{ id: number }>('/api/repo/bind', {
      owner: o,
      name: n,
      keywords: splitList(keywords.value),
      authors: splitList(authors.value, true),
    })
    if (repoToken.value.trim()) {
      await postJSON('/api/local/repo-token', { owner: o, name: n, token: repoToken.value.trim() })
    }
    owner.value = ''
    name.value = ''
    keywords.value = ''
    authors.value = ''
    repoToken.value = ''
    await refresh()
    if (autoPr.value && res.id) {
      msg.value = '绑定成功，正在发送 workflow PR…'
      await sendPr({ id: res.id, owner: o, name: n })
    } else {
      msg.value = '绑定成功'
    }
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

function kwText(r: Repo): string {
  return (r.keywords || []).join('，')
}

function authorsText(r: Repo): string {
  return (r.authors || []).join('，')
}

function startEdit(r: Repo) {
  editMap.value[r.id] = { keywords: kwText(r), authors: authorsText(r), token: '' }
}

function isEditing(r: Repo): boolean {
  return Object.prototype.hasOwnProperty.call(editMap.value, r.id)
}

function cancelEdit(r: Repo) {
  delete editMap.value[r.id]
}

async function saveEdit(r: Repo) {
  busyId.value = r.id
  err.value = ''
  try {
    const edit = editMap.value[r.id]
    await postJSON('/api/repo/update', {
      id: r.id,
      keywords: splitList(edit.keywords),
      authors: splitList(edit.authors, true),
    })
    if (edit.token.trim()) {
      await postJSON('/api/local/repo-token', { owner: r.owner, name: r.name, token: edit.token.trim() })
    }
    const idx = repos.value.findIndex((x) => x.id === r.id)
    if (idx >= 0) {
      const next = {
        ...repos.value[idx],
        keywords: splitList(edit.keywords),
        authors: splitList(edit.authors, true),
      }
      if (edit.token.trim()) {
        next.has_repo_token = true
        next.token_source = 'repo'
      }
      repos.value[idx] = next
    }
    delete editMap.value[r.id]
    await refresh()
    // 关键词/白名单变更后，重发 PR 以更新 workflow 内的关键词
    if (autoPr.value) await sendPr({ id: r.id, owner: r.owner, name: r.name })
  } catch (e: any) {
    err.value = e?.message || String(e)
  } finally {
    busyId.value = null
  }
}

async function removeRepoToken(r: Repo, index: number) {
  const hint = (r.repo_token_hints || [])[index] || ''
  if (!confirm(`确定删除 ${r.owner}/${r.name} 的第 ${index + 1} 个令牌（${hint}）吗？`)) return
  err.value = ''
  try {
    await postJSON('/api/local/repo-token', { owner: r.owner, name: r.name, action: 'remove', index })
    await refresh()
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

async function clearToken(r: Repo) {
  if (!confirm(`确定清空 ${r.owner}/${r.name} 的全部仓库专属令牌吗？`)) return
  err.value = ''
  try {
    await postJSON('/api/local/repo-token', { owner: r.owner, name: r.name, action: 'clear' })
    msg.value = '已清空该仓库的专属令牌'
    await refresh()
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

async function unbind(r: Repo) {
  if (!confirm(`确定解绑 ${r.owner}/${r.name} 吗？解绑后该仓库可被其他账号绑定。`)) return
  err.value = ''
  try {
    await postJSON('/api/repo/unbind', { id: r.id })
    await postJSON('/api/local/repo-token', { owner: r.owner, name: r.name, action: 'clear' })
    delete prMap.value[r.id]
    await refresh()
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

async function downloadWorkflow(r: Repo) {
  err.value = ''
  try {
    const file = await fetchWorkflow(r.id)
    downloadText(file.filename, file.text, 'text/yaml')
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

async function copyFallbackContent() {
  if (!fallback.value) return
  try {
    await navigator.clipboard.writeText(fallback.value.content)
    msg.value = 'workflow 内容已复制，粘贴到 GitHub 新建文件页即可'
  } catch {
    err.value = '复制失败，请手动复制'
  }
}

onActivated(refresh)
</script>

<template>
  <div class="repos">
    <div v-if="err" class="alert">{{ err }}</div>
    <div v-if="msg" class="ok">{{ msg }}</div>

    <div class="panel form-panel">
      <h3>绑定新仓库（绑定后自动发送 workflow PR）</h3>
      <div class="grid">
        <div>
          <label>仓库作者（owner）</label>
          <input v-model.trim="owner" placeholder="例如 crazying-dev" />
        </div>
        <div>
          <label>仓库名称（repo）</label>
          <input v-model.trim="name" placeholder="例如 CodeVoyage" />
        </div>
        <div>
          <label>触发关键词（逗号分隔）</label>
          <input v-model="keywords" placeholder="@ai，需要修复" />
        </div>
        <div>
          <label>触发者白名单（可选）</label>
          <input v-model="authors" placeholder="GitHub 用户名，默认仅仓库 owner" />
        </div>
        <div>
          <label>细粒度令牌（可选，仅存本机，可多个）</label>
          <input v-model="repoToken" type="password" placeholder="github_pat_...（只授权本仓库）" />
        </div>
      </div>
      <label class="checkbox">
        <input v-model="autoPr" type="checkbox" />
        绑定后自动发送 PR（推荐；也可稍后点「重发 PR」）
      </label>
      <button class="primary" @click="bind">绑定并发送 PR</button>
    </div>

    <div class="panel">
      <div class="row head-row">
        <h3>已绑定仓库</h3>
        <div class="spacer"></div>
        <button :disabled="checkingWf || !repos.length" @click="refreshWorkflowStatuses">
          {{ checkingWf ? '检查中…' : '刷新状态' }}
        </button>
      </div>
      <p v-if="loading" class="muted">正在从远端加载仓库列表…</p>
      <p v-if="wfReason" class="hint">提示：{{ wfReason }}</p>
      <table v-if="repos.length" class="list">
        <thead>
          <tr>
            <th>仓库</th>
            <th>workflow 状态</th>
            <th>令牌</th>
            <th>关键词</th>
            <th>触发者白名单</th>
            <th>任务数</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="r in repos" :key="r.id">
            <tr>
              <td class="mono">{{ r.owner }}/{{ r.name }}</td>
              <td>
                <span :class="['status-tag', wfClass(r.id)]">{{ WF_TEXT[wfStatus[r.id] || 'unknown'] }}</span>
              </td>
              <td>
                <span :class="['status-tag', tokenClass(r)]">{{ TOKEN_TEXT[r.token_source] }}</span>
                <div v-if="r.repo_token_hints && r.repo_token_hints.length" class="token-list">
                  <div v-for="(h, i) in r.repo_token_hints" :key="h + i" class="token-item">
                    <span class="mono small">{{ i + 1 }}. {{ h }}</span>
                    <button class="danger tiny" @click="removeRepoToken(r, i)">删除</button>
                  </div>
                </div>
                <div v-else-if="r.global_token_count" class="muted small">回退全局令牌（{{ r.global_token_count }} 个）</div>
                <div v-else class="muted small">无令牌</div>
              </td>
              <td>
                <input v-if="isEditing(r)" v-model="editMap[r.id].keywords" class="kw-input" placeholder="逗号分隔多个关键词" />
                <span v-else>{{ kwText(r) || '—' }}</span>
              </td>
              <td>
                <input v-if="isEditing(r)" v-model="editMap[r.id].authors" class="kw-input" placeholder="逗号分隔，留空=仅 owner" />
                <span v-else>{{ authorsText(r) || '仅 owner' }}</span>
              </td>
              <td>{{ r.issue_count }}</td>
              <td>
                <div class="row wrap-actions">
                  <template v-if="isEditing(r)">
                    <input v-model="editMap[r.id].token" class="kw-input" type="password" placeholder="追加细粒度令牌（不覆盖已有）" />
                    <button class="primary" :disabled="busyId === r.id" @click="saveEdit(r)">保存</button>
                    <button @click="cancelEdit(r)">取消</button>
                  </template>
                  <template v-else>
                    <button
                      class="primary"
                      :disabled="!!prMap[r.id]?.running"
                      @click="sendPr({ id: r.id, owner: r.owner, name: r.name })"
                    >
                      {{ prMap[r.id]?.running ? '发送中…' : (prMap[r.id]?.ok ? '重发 PR' : '发送 PR') }}
                    </button>
                    <button @click="startEdit(r)">编辑</button>
                    <button v-if="r.has_repo_token" class="danger" @click="clearToken(r)">清除令牌</button>
                    <button @click="downloadWorkflow(r)">下载</button>
                    <button class="danger" @click="unbind(r)">解绑</button>
                  </template>
                </div>
              </td>
            </tr>
            <tr v-if="prMap[r.id]" class="detail-row">
              <td colspan="7">
                <div v-if="prMap[r.id].ok" class="ok">
                  PR 已创建：<a :href="prMap[r.id].pr_url" target="_blank">{{ prMap[r.id].pr_url }}</a>
                  <span class="muted">（分支 {{ prMap[r.id].branch }}；{{ prMap[r.id].reason }}）请前往 GitHub 合并后点「刷新状态」</span>
                </div>
                <div v-else-if="prMap[r.id].running" class="muted">正在执行：准备特性分支 → 提交 → 推送 → 创建 PR…</div>
                <div v-else>
                  <div class="alert">{{ prMap[r.id].reason }}</div>
                  <div class="row">
                    <button class="danger" @click="sendPr({ id: r.id, owner: r.owner, name: r.name })">重试</button>
                  </div>
                </div>
                <ol v-if="prMap[r.id].steps && prMap[r.id].steps.length" class="steps">
                  <li v-for="(s, i) in prMap[r.id].steps" :key="i">{{ s }}</li>
                </ol>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      <p v-else-if="!loading" class="muted">尚未绑定任何仓库。</p>
    </div>

    <div v-if="fallback" class="fallback-box">
      <h4>没有可用令牌，可改用网页提交（效果等同 PR）</h4>
      <p class="hint">
        仓库：<span class="mono">{{ fallback.repo }}</span>；路径：<span class="mono">{{ fallback.path }}</span>。
        登录 GitHub 后在网页新建该文件并粘贴内容，提交时会自动建分支并提示发起 PR。
      </p>
      <div class="row wrap-actions">
        <button class="primary" @click="copyFallbackContent">复制 workflow 内容</button>
        <a :href="fallback.new_file_urls[0]" target="_blank" rel="noreferrer">打开新建文件页（main）</a>
        <a :href="fallback.new_file_urls[1]" target="_blank" rel="noreferrer">打开新建文件页（master）</a>
        <a :href="fallback.repo_url" target="_blank" rel="noreferrer">打开仓库</a>
      </div>
      <p class="hint">提交后点本页「刷新状态」，显示「workflow 已装」即完成。</p>
    </div>

    <div class="panel">
      <h3>操作指引</h3>
      <ol class="guide">
        <li>绑定仓库时填好关键词与触发者白名单（留空=仅仓库 owner 可触发），绑定后会自动发送 PR。</li>
        <li>PR 由四步完成：准备特性分支 → 提交代码 → 推送远端 → 调用接口创建 PR；每步结果会显示在仓库下方。</li>
        <li>打开 PR 链接 <strong>合并 PR</strong>（未合并前 workflow 不生效）；合并后点「刷新状态」看到「workflow 已装」。</li>
        <li>在仓库 <span class="mono">Settings → Secrets and variables → Actions</span> 配置：</li>
        <li class="sub mono">BACKEND_WEBHOOK_URL = 服务端地址 + /api/Github/Issue</li>
        <li class="sub mono">BACKEND_WEBHOOK_SECRET = 与服务端 .env 中 WEBHOOK_SECRET 一致</li>
        <li>改了关键词/白名单或令牌后，点该仓库的「重发 PR」即可更新 workflow 文件。</li>
      </ol>
    </div>
  </div>
</template>

<style scoped>
.ok {
  color: var(--ok);
  margin-bottom: 10px;
}

.head-row {
  margin-bottom: 10px;
}

.head-row h3 {
  margin: 0;
}

.form-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
}

label {
  display: block;
  color: var(--text-dim);
  font-size: 13px;
  margin-bottom: 6px;
}

label.checkbox {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
}

label.checkbox input {
  width: auto;
}

.kw-input {
  min-width: 200px;
}

.wrap-actions {
  flex-wrap: wrap;
}

.small {
  font-size: 11px;
}

.token-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 4px;
}

.token-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

button.tiny {
  padding: 1px 8px;
  font-size: 11px;
}

.detail-row td {
  background: rgb(255 255 255 / 0.02);
}

ol.steps {
  margin: 6px 0 0 20px;
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.8;
}

.fallback-box {
  border: 1px solid var(--warn);
  border-radius: 8px;
  padding: 12px;
}

.fallback-box h4 {
  margin: 0 0 6px;
  font-size: 14px;
}

ol.guide {
  margin: 10px 0 6px 20px;
  line-height: 1.9;
}

ol.guide li.sub {
  margin-left: 14px;
}

.mono {
  color: var(--text-dim);
}
</style>
