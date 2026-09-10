<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { fetchWorkflow, downloadText, postJSON } from '../api'

interface Repo {
  id: number
  owner: string
  name: string
  keywords: string[]
  authors: string[]
}

interface CheckResult {
  installed: boolean
  up_to_date: boolean
  file: string
  files: { name: string; path: string; is_codevoyage: boolean; has_author_check: boolean }[]
  reason: string
}

interface PrResult {
  pr_url: string
  branch: string
  filename: string
  path: string
  existed: boolean
}

type Status = 'unknown' | 'installed' | 'outdated' | 'missing' | 'pending'

const repos = ref<Repo[]>([])
const selectedId = ref<number | ''>('')
const preview = ref('')
const filename = ref('codevoyage-workflow.yml')
const loading = ref(false)
const err = ref('')
const msg = ref('')

const status = ref<Status>('unknown')
const check = ref<CheckResult | null>(null)
const checking = ref(false)

const installing = ref(false)
const installErr = ref('')
const prResult = ref<PrResult | null>(null)

const selected = computed(() => repos.value.find((r) => r.id === selectedId.value) || null)

const STATUS_TEXT: Record<Status, string> = {
  unknown: '未检查',
  installed: '已安装',
  outdated: '已安装（旧版）',
  missing: '未安装',
  pending: 'PR 待合并',
}

const statusClass = computed(() => {
  switch (status.value) {
    case 'installed':
      return 'ok'
    case 'outdated':
      return 'waiting'
    case 'missing':
      return 'failed'
    case 'pending':
      return 'running'
    default:
      return ''
  }
})

async function loadRepos() {
  err.value = ''
  try {
    const j = await postJSON<{ repos: Repo[] }>('/api/repo/list', {})
    repos.value = j.repos
    if (j.repos.length && selectedId.value === '') {
      selectedId.value = j.repos[0].id
      await generate()
      await refreshStatus()
    }
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

async function generate() {
  if (!selectedId.value) return
  loading.value = true
  err.value = ''
  msg.value = ''
  try {
    const file = await fetchWorkflow(selectedId.value)
    preview.value = file.text
    filename.value = file.filename
  } catch (e: any) {
    err.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
}

async function copy() {
  try {
    await navigator.clipboard.writeText(preview.value)
    msg.value = '已复制到剪贴板'
  } catch {
    err.value = '复制失败，请手动选择文本复制'
  }
}

function download() {
  downloadText(filename.value, preview.value, 'text/yaml')
}

/** 通过 GitHub API 识别仓库是否已添加 workflow（手动刷新也走这里） */
async function refreshStatus() {
  if (!selected.value) return
  checking.value = true
  err.value = ''
  try {
    const res = await postJSON<CheckResult>('/api/repo/check-workflow', {
      owner: selected.value.owner,
      name: selected.value.name,
    })
    check.value = res
    if (res.installed) {
      status.value = res.up_to_date ? 'installed' : 'outdated'
      prResult.value = null // 已合并，清除「待合并」
    } else {
      status.value = prResult.value ? 'pending' : 'missing'
    }
  } catch (e: any) {
    err.value = e?.message || String(e)
  } finally {
    checking.value = false
  }
}

/** 生成后直接提交 PR */
async function install() {
  if (!selected.value || !selectedId.value) return
  installing.value = true
  installErr.value = ''
  msg.value = ''
  try {
    const res = await postJSON<{
      ok: boolean
      reason?: string
      pr_url?: string
      branch?: string
      filename?: string
      path?: string
      existed?: boolean
    }>('/api/repo/install-workflow', {
      id: selectedId.value,
      owner: selected.value.owner,
      name: selected.value.name,
    })
    if (!res.ok) {
      installErr.value = res.reason || '提交失败'
      return
    }
    prResult.value = {
      pr_url: res.pr_url || '',
      branch: res.branch || '',
      filename: res.filename || filename.value,
      path: res.path || '',
      existed: !!res.existed,
    }
    status.value = 'pending'
    msg.value = 'PR 已创建，请前往合并'
  } catch (e: any) {
    installErr.value = e?.message || String(e)
  } finally {
    installing.value = false
  }
}

function onSelect() {
  check.value = null
  prResult.value = null
  installErr.value = ''
  status.value = 'unknown'
  msg.value = ''
  generate()
  refreshStatus()
}

onMounted(loadRepos)
</script>

<template>
  <div class="wf">
    <div v-if="err" class="alert">{{ err }}</div>
    <div v-if="msg" class="ok">{{ msg }}</div>

    <div class="panel">
      <div class="row head-row">
        <h3>Workflow 生成器</h3>
        <div class="spacer"></div>
        <span :class="['status-tag', statusClass]">{{ STATUS_TEXT[status] }}</span>
        <button :disabled="checking || !selected" @click="refreshStatus">
          {{ checking ? '检查中…' : '刷新状态' }}
        </button>
      </div>
      <p class="muted">
        为绑定的仓库生成 GitHub Actions workflow 并可直接提交 PR。
        合并 PR 后 workflow 才会生效；执行前请在仓库 Secrets 配置
        <span class="mono">BACKEND_WEBHOOK_URL</span> 与 <span class="mono">BACKEND_WEBHOOK_SECRET</span>。
      </p>

      <div v-if="!repos.length" class="muted">还没有绑定的仓库，请先到「仓库绑定」页添加。</div>

      <template v-else>
        <div class="row toolbar">
          <span class="muted">选择仓库</span>
          <select v-model.number="selectedId" @change="onSelect">
            <option v-for="r in repos" :key="r.id" :value="r.id">{{ r.owner }}/{{ r.name }}</option>
          </select>
          <button :disabled="loading" @click="generate">重新生成</button>
          <button :disabled="!preview" @click="copy">复制</button>
          <button :disabled="!preview" @click="download">下载</button>
          <button class="primary" :disabled="installing" @click="install">
            {{ installing ? '提交中…' : '提交 PR（安装）' }}
          </button>
        </div>

        <div v-if="selected" class="meta">
          <span class="muted">关键词：</span>{{ selected.keywords.join('，') || '—' }}
          <span class="muted sep">触发者白名单：</span>{{ selected.authors.join('，') || '仅仓库 owner' }}
        </div>

        <div v-if="installErr" class="alert retry-box">
          <span>{{ installErr }}</span>
          <button class="danger" :disabled="installing" @click="install">重试</button>
        </div>

        <div v-if="prResult" class="pr-box">
          <div class="row">
            <span class="status-tag running">PR 待合并</span>
            <a :href="prResult.pr_url" target="_blank">{{ prResult.pr_url }}</a>
          </div>
          <p class="hint">
            {{ prResult.existed ? '已提交更新版 workflow' : '已提交 workflow' }}：
            <span class="mono">{{ prResult.path }}</span>（分支 {{ prResult.branch }}）
          </p>
          <p class="hint">请前往 GitHub 合并该 PR；合并后点「刷新状态」应显示为「已安装」。</p>
        </div>

        <pre class="log preview">{{ loading ? '生成中…' : preview }}</pre>
      </template>
    </div>

    <div class="panel">
      <h3>操作指引</h3>
      <ol class="guide">
        <li>在「仓库绑定」页确认关键词与触发者白名单正确（白名单留空表示仅仓库 owner 可触发）。</li>
        <li>本页选择仓库 → 点「提交 PR（安装）」，系统会自动创建分支并把 workflow 提交到
          <span class="mono">.github/workflows/codevoyage-&lt;owner&gt;-&lt;repo&gt;.yml</span>。</li>
        <li>打开返回的 PR 链接并 <strong>合并 PR</strong>（未合并前 workflow 不会生效）。</li>
        <li>合并前在仓库 <span class="mono">Settings → Secrets and variables → Actions</span> 添加：
          <div class="mono ind">BACKEND_WEBHOOK_URL = 你的服务端地址 + /api/Github/Issue</div>
          <div class="mono ind">BACKEND_WEBHOOK_SECRET = 与服务端 .env 中 WEBHOOK_SECRET 一致</div>
        </li>
        <li>回到本页点「刷新状态」，显示「已安装」即配置完成；显示「已安装（旧版）」说明需要重新提交覆盖。</li>
        <li>不想自动提交时，也可用「下载」把 .yml 放到仓库
          <span class="mono">.github/workflows/</span> 目录后自行提交。</li>
      </ol>
      <p class="hint">提交 PR 使用的是「本地配置」里的 GitHub Token，仅在你这台机器上使用，不会上传到服务端。</p>
    </div>

    <div v-if="check" class="panel">
      <h3>仓库检查结果</h3>
      <div class="row result-row">
        <span :class="['status-tag', check.installed ? 'ok' : 'failed']">
          {{ check.installed ? '已安装' : '未安装' }}
        </span>
        <span v-if="check.installed" :class="['status-tag', check.up_to_date ? 'ok' : 'waiting']">
          {{ check.up_to_date ? '含作者校验（最新版）' : '旧版：缺少作者校验，建议重新提交' }}
        </span>
        <span v-if="check.file" class="muted mono">{{ check.file }}</span>
      </div>
      <p v-if="check.reason" class="hint">提示：{{ check.reason }}</p>

      <table v-if="check.files.length" class="list">
        <thead>
          <tr>
            <th>文件</th>
            <th>是否 CodeVoyage</th>
            <th>作者校验</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="f in check.files" :key="f.path">
            <td class="mono">{{ f.name }}</td>
            <td>{{ f.is_codevoyage ? '是' : '否' }}</td>
            <td>{{ f.has_author_check ? '有' : '无' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.wf {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

h3 {
  margin: 0;
  font-size: 15px;
}

.head-row {
  margin-bottom: 6px;
}

.ok {
  color: var(--ok);
}

.toolbar {
  margin: 14px 0 10px;
  flex-wrap: wrap;
}

select {
  width: 240px;
}

.meta {
  font-size: 13px;
  margin-bottom: 10px;
}

.sep {
  margin-left: 14px;
}

.preview {
  max-height: 400px;
  margin-top: 10px;
}

.retry-box {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 10px 0;
}

.pr-box {
  border: 1px solid var(--accent);
  border-radius: 8px;
  padding: 12px;
  margin: 10px 0;
}

.result-row {
  flex-wrap: wrap;
  margin-bottom: 8px;
}

table.list {
  margin-top: 10px;
}

ol.guide {
  margin: 10px 0 6px 20px;
  line-height: 1.9;
}

ol.guide li {
  color: var(--text);
}

.ind {
  margin-left: 14px;
}

.mono {
  color: var(--text-dim);
}
</style>
