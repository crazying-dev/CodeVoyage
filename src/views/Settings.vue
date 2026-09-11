<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { postJSON } from '../api'
import { state, loadStatus } from '../store'

const err = ref('')
const ok = ref('')

const githubToken = ref('')
const tokens = ref<string[]>([])
const llmKey = ref('')
const llmBase = ref('')
const llmModel = ref('')
const gitName = ref('')
const gitEmail = ref('')
const savingLocal = ref(false)

const checkingGh = ref(false)
const ghCheck = ref<{
  ok: boolean
  kind: string
  login: string
  scopes: string[]
  hints: string[]
  reason: string
} | null>(null)

// 诊断信息：本地存储路径 + 登录态有效性
const diag = ref<{
  remote: string
  logged_in: boolean
  session: { checked: boolean; valid: boolean; reason: string }
  storage: { base_dir: string; source: string; writable: boolean; files: { name: string; path: string; exists: boolean; size: number; mtime: string }[] }
  network: { proxy: string; system_proxy_ignored: boolean; retry: number; retry_interval: number }
} | null>(null)
const loadingDiag = ref(false)

const KIND_TEXT: Record<string, string> = {
  classic: '传统令牌（classic）',
  fine_grained: '细粒度令牌（fine-grained）',
  unknown: '未识别的令牌类型',
}

const githubSet = computed(() => !!state.status?.github_configured)
const llmSet = computed(() => !!state.status?.llm_configured)

onMounted(async () => {
  if (!state.loaded) await loadStatus()
  tokens.value = state.status?.github_tokens || []
  llmBase.value = state.status?.llm_base_url || ''
  llmModel.value = state.status?.llm_model || ''
  gitName.value = state.status?.git_name || ''
  gitEmail.value = state.status?.git_email || ''
  runDiagnostics()
})

/** 全局令牌列表：添加 / 删除 / 调整顺序（顺序即回退尝试顺序） */
async function manageTokens(payload: Record<string, unknown>) {
  err.value = ''
  ok.value = ''
  try {
    const j = await postJSON<{ github_tokens: string[] }>('/api/local/tokens', payload)
    tokens.value = j.github_tokens || []
    if (state.status) {
      state.status.github_tokens = j.github_tokens || []
      state.status.github_configured = (j.github_tokens || []).length > 0
    }
    ghCheck.value = null
    ok.value = '令牌列表已更新'
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

async function addToken() {
  const token = githubToken.value.trim()
  if (!token) return
  await manageTokens({ action: 'add', token })
  githubToken.value = ''
}

async function removeToken(index: number) {
  const hint = tokens.value[index] || ''
  if (!confirm(`确定删除第 ${index + 1} 个令牌（${hint}）吗？`)) return
  await manageTokens({ action: 'remove', index })
}

async function moveToken(index: number, delta: number) {
  await manageTokens({ action: 'move', index, delta })
}

async function clearTokens() {
  if (!confirm('确定清空全部传统令牌吗？')) return
  await manageTokens({ action: 'clear' })
}

async function runDiagnostics() {
  loadingDiag.value = true
  try {
    diag.value = await postJSON('/api/local/diagnostics', {})
  } catch (e: any) {
    err.value = e?.message || String(e)
  } finally {
    loadingDiag.value = false
  }
}

async function saveLocal() {
  err.value = ''
  ok.value = ''
  const payload: Record<string, unknown> = {}
  if (githubToken.value !== '') payload.github_token = githubToken.value
  if (llmKey.value !== '') payload.llm_api_key = llmKey.value
  if (llmBase.value !== '') payload.llm_base_url = llmBase.value
  if (llmModel.value !== '') payload.llm_model = llmModel.value
  if (gitName.value !== '') payload.git_name = gitName.value
  if (gitEmail.value !== '') payload.git_email = gitEmail.value
  if (!Object.keys(payload).length) {
    err.value = '没有需要保存的内容'
    return
  }
  savingLocal.value = true
  try {
    await postJSON('/api/local/config', payload)
    ok.value = '本地配置已保存'
    githubToken.value = ''
    llmKey.value = ''
    ghCheck.value = null
    await loadStatus()
  } catch (e: any) {
    err.value = e?.message || String(e)
  } finally {
    savingLocal.value = false
  }
}

async function clearKey(key: 'github_token' | 'llm_api_key') {
  const label = key === 'github_token' ? 'GitHub Token' : 'LLM API Key'
  if (!confirm(`确定删除已保存的 ${label} 吗？删除后 Agent 将无法执行对应操作。`)) return
  err.value = ''
  ok.value = ''
  try {
    await postJSON('/api/local/config', { clear: [key] })
    ok.value = `${label} 已删除`
    ghCheck.value = null
    await loadStatus()
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

async function verifyGithub() {
  err.value = ''
  ok.value = ''
  checkingGh.value = true
  try {
    ghCheck.value = await postJSON('/api/local/github-status', {})
  } catch (e: any) {
    err.value = e?.message || String(e)
  } finally {
    checkingGh.value = false
  }
}
</script>

<template>
  <div class="settings">
    <div v-if="err" class="alert">{{ err }}</div>
    <div v-if="ok" class="ok">{{ ok }}</div>

    <div class="panel">
      <div class="row head-row">
        <h3>运行与存储诊断</h3>
        <div class="spacer"></div>
        <button :disabled="loadingDiag" @click="runDiagnostics">{{ loadingDiag ? '检查中…' : '重新检查' }}</button>
      </div>
      <template v-if="diag">
        <p class="hint">
          远端服务（固定）：<span class="mono">{{ diag.remote }}</span>
        </p>
        <p class="hint">
          本地存储目录：<span class="mono">{{ diag.storage.base_dir }}</span>
          （来源 {{ diag.storage.source }}，{{ diag.storage.writable ? '可写' : '不可写' }}）
        </p>
        <p v-if="diag.logged_in" class="hint">
          登录态：
          <span v-if="!diag.session.checked" class="status-tag waiting">无法校验（{{ diag.session.reason }}）</span>
          <span v-else-if="diag.session.valid" class="status-tag ok">有效</span>
          <span v-else class="status-tag failed">已失效：{{ diag.session.reason }}</span>
        </p>
        <p v-else class="hint">登录态：<span class="status-tag">未登录</span></p>

        <p class="hint">
          网络：<span v-if="diag.network.proxy" class="mono">使用代理 {{ diag.network.proxy }}</span>
          <span v-else>已忽略系统代理（直连，避免失效代理拖死）</span>
          ；失败重试 {{ diag.network.retry }} 次、间隔 {{ diag.network.retry_interval }}s
        </p>
        <p v-if="!diag.network.proxy" class="hint">
          如需走代理，请设置环境变量 <span class="mono">CODEVOYAGE_PROXY</span>（例如
          <span class="mono">http://127.0.0.1:7890</span>）后重启客户端。
        </p>

        <table class="list">
          <thead>
            <tr><th>本地文件</th><th>是否存在</th><th>大小</th><th>最后修改</th></tr>
          </thead>
          <tbody>
            <tr v-for="f in diag.storage.files" :key="f.path">
              <td>{{ f.name }}</td>
              <td>
                <span :class="['status-tag', f.exists ? 'ok' : 'failed']">{{ f.exists ? '有' : '无' }}</span>
              </td>
              <td class="muted">{{ f.size }} B</td>
              <td class="muted">{{ f.mtime || '—' }}</td>
            </tr>
          </tbody>
        </table>
        <p class="hint">
          「登录凭证」「local.json」「secret.key」必须长期保留在同一目录；若该目录每次运行都变化（例如受限环境把
          ~ 映射到临时目录），就会出现“每次都要重新登录 / 配置丢失”。可设置环境变量
          <span class="mono">CODEVOYAGE_HOME</span> 指向固定目录后重启客户端。
        </p>
      </template>
      <p v-else class="muted">正在读取诊断信息…</p>
    </div>

    <div class="panel">
      <h3>传统令牌（全局）</h3>
      <p class="muted">
        仅保存在本机 ~/.CodeVoyage（混淆落盘，绝不外传）。用于未单独绑定细粒度令牌的仓库；
        细粒度令牌请到「仓库绑定」页随仓库填写。
      </p>

      <label>GitHub 传统令牌（classic，可添加多个，按顺序回退尝试）</label>
      <table v-if="tokens.length" class="list">
        <thead>
          <tr><th>#</th><th>令牌</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="(t, i) in tokens" :key="t + i">
            <td>{{ i + 1 }}</td>
            <td class="mono">{{ t }}</td>
            <td>
              <div class="row wrap">
                <button :disabled="i === 0" @click="moveToken(i, -1)">上移</button>
                <button :disabled="i === tokens.length - 1" @click="moveToken(i, 1)">下移</button>
                <button class="danger" @click="removeToken(i)">删除</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="hint">未配置（该仓库未单独绑定细粒度令牌时，将回退到这里）</p>
      <div class="row input-row">
        <input v-model="githubToken" type="password" placeholder="ghp_... 输入后点「添加」（不会覆盖已有令牌）"
               @keyup.enter="addToken" />
        <button class="primary" :disabled="!githubToken.trim()" @click="addToken">添加</button>
        <button :disabled="!tokens.length || checkingGh" @click="verifyGithub">
          {{ checkingGh ? '校验中…' : '验证第一个' }}
        </button>
        <button class="danger" :disabled="!tokens.length" @click="clearTokens">清空</button>
      </div>
      <div v-if="ghCheck" class="gh-check">
        <p v-if="ghCheck.ok" class="hint">
          <span class="status-tag ok">{{ KIND_TEXT[ghCheck.kind] || ghCheck.kind }}</span>
          已验证，GitHub 账号：<strong>{{ ghCheck.login }}</strong>
          <template v-if="ghCheck.scopes.length">；scopes：{{ ghCheck.scopes.join(', ') }}</template>
        </p>
        <p v-else class="alert">{{ ghCheck.reason }}</p>
        <ul v-if="ghCheck.hints.length" class="hints">
          <li v-for="h in ghCheck.hints" :key="h">{{ h }}</li>
        </ul>
        <p class="hint">
          <a href="https://github.com/settings/tokens" target="_blank">管理传统令牌</a>
          ·
          <a href="https://github.com/settings/personal-access-tokens" target="_blank">管理细粒度令牌</a>
        </p>
      </div>

      <button class="primary" :disabled="savingLocal" @click="saveLocal">保存传统令牌</button>
    </div>

    <div class="panel">
      <h3>AI 配置（独立绑定）</h3>
      <p class="muted">与 GitHub 令牌互不相干，单独配置；同样只存本机，可随时删除。</p>

      <label>LLM API Key</label>
      <div class="row input-row">
        <input v-model="llmKey" type="password"
               :placeholder="llmSet ? '已配置，输入新值即覆盖' : 'sk-...'" />
        <button class="danger" :disabled="!llmSet" @click="clearKey('llm_api_key')">删除</button>
      </div>
      <div class="row input-row">
        <span v-if="llmSet" class="muted mono">当前：{{ state.status?.llm_api_key_hint }}</span>
        <span v-else class="hint">未配置</span>
      </div>

      <div class="grid2">
        <div>
          <label>LLM Base URL</label>
          <input v-model="llmBase" placeholder="https://api.deepseek.com" />
        </div>
        <div>
          <label>LLM 模型</label>
          <input v-model="llmModel" placeholder="deepseek-v4-flash" />
        </div>
      </div>

      <p class="hint">
        Agent 执行任务需要：GitHub 令牌（克隆/推送/提交 PR，仓库专属优先）与 LLM Key（代码修改）。
      </p>
      <button class="primary" :disabled="savingLocal" @click="saveLocal">保存 AI 配置</button>
    </div>

    <div class="panel">
      <h3>提交身份（commit / PR）</h3>
      <p class="muted">
        提交 workflow PR 与 Agent 生成 PR 时使用的提交者身份。默认使用 3890320020@qq.com。
      </p>
      <div class="grid2">
        <div>
          <label>提交者名称</label>
          <input v-model="gitName" placeholder="CodeVoyage AI" />
        </div>
        <div>
          <label>提交者邮箱</label>
          <input v-model="gitEmail" placeholder="3890320020@qq.com" />
        </div>
      </div>
      <p class="hint">该身份只影响提交记录（author/committer），与 GitHub 账号登录无关。</p>
      <button class="primary" :disabled="savingLocal" @click="saveLocal">保存提交身份</button>
    </div>
  </div>
</template>

<style scoped>
.ok {
  color: var(--ok);
  margin-bottom: 10px;
}

.settings {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 760px;
}

h3 {
  margin-bottom: 8px;
}

label {
  display: block;
  color: var(--text-dim);
  font-size: 13px;
  margin: 12px 0 6px;
}

.input-row {
  margin: 6px 0;
}

.row.wrap {
  flex-wrap: wrap;
}

.gh-check {
  margin: 6px 0 4px;
}

.head-row {
  margin-bottom: 8px;
}

.head-row h3 {
  margin: 0;
}

ul.hints {
  margin: 6px 0 6px 20px;
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.7;
}

.grid2 {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}
</style>
