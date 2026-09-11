<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { postJSON } from '../api'

interface Diag {
  remote: string
  logged_in: boolean
  session: { checked: boolean; valid: boolean; reason: string }
  storage: {
    base_dir: string
    source: string
    writable: boolean
    files: { name: string; path: string; exists: boolean; size: number; mtime: string }[]
  }
  network: { proxy: string; system_proxy_ignored: boolean; retry: number; retry_interval: number }
  agent: Record<string, any>
}

interface LatencyProbe {
  ok: boolean
  status: number
  ms: number
  reason: string
}

const diag = ref<Diag | null>(null)
const gh = ref<Record<string, any> | null>(null)
const latency = ref<{ api: LatencyProbe; git: LatencyProbe } | null>(null)
const err = ref('')
const msg = ref('')
const busy = ref('')
const token = ref('')

async function loadDiag() {
  err.value = ''
  busy.value = 'diag'
  try {
    diag.value = await postJSON<Diag>('/api/local/diagnostics', {})
  } catch (e: any) {
    err.value = e?.message || String(e)
  } finally {
    busy.value = ''
  }
}

async function checkToken() {
  err.value = ''
  busy.value = 'token'
  try {
    gh.value = await postJSON('/api/local/github-status', token.value.trim() ? { token: token.value.trim() } : {})
  } catch (e: any) {
    err.value = e?.message || String(e)
  } finally {
    busy.value = ''
  }
}

async function checkLatency() {
  busy.value = 'latency'
  try {
    latency.value = await postJSON('/api/local/github-latency', {})
  } catch (e: any) {
    err.value = e?.message || String(e)
  } finally {
    busy.value = ''
  }
}

async function syncCredentials() {
  err.value = ''
  msg.value = ''
  busy.value = 'sync'
  try {
    const res = await postJSON<{ github_token_count: number; llm_count: number }>(
      '/api/local/credentials/sync',
      {},
    )
    msg.value = `已同步：GitHub 令牌 ${res.github_token_count} 个、LLM 配置 ${res.llm_count} 条`
  } catch (e: any) {
    err.value = e?.message || String(e)
  } finally {
    busy.value = ''
  }
}

function latClass(p?: LatencyProbe): string {
  if (!p || !p.ok) return 'failed'
  if (p.ms < 400) return 'ok'
  if (p.ms < 1200) return 'waiting'
  return 'failed'
}

onMounted(() => {
  loadDiag()
  checkToken()
  checkLatency()
})
</script>

<template>
  <div class="diagnostics">
    <div v-if="err" class="alert">{{ err }}</div>
    <div v-if="msg" class="ok">{{ msg }}</div>

    <div class="panel">
      <div class="row head-row">
        <h3>运行与存储</h3>
        <div class="spacer"></div>
        <button :disabled="busy === 'diag'" @click="loadDiag">{{ busy === 'diag' ? '检查中…' : '重新检查' }}</button>
      </div>
      <template v-if="diag">
        <p class="hint">远端服务：<span class="mono">{{ diag.remote }}</span></p>
        <p class="hint">
          本地存储：<span class="mono">{{ diag.storage.base_dir }}</span>
          （来源 {{ diag.storage.source }}，{{ diag.storage.writable ? '可写' : '不可写' }}）
        </p>
        <p class="hint">
          登录态：
          <span v-if="!diag.logged_in" class="status-tag failed">未登录</span>
          <span v-else-if="!diag.session.checked" class="status-tag waiting">无法校验（{{ diag.session.reason }}）</span>
          <span v-else-if="diag.session.valid" class="status-tag ok">有效</span>
          <span v-else class="status-tag failed">已失效：{{ diag.session.reason }}</span>
        </p>
        <p class="hint">
          网络：{{ diag.network.system_proxy_ignored ? '已忽略系统代理' : `使用代理 ${diag.network.proxy}` }}；
          失败重试 {{ diag.network.retry }} 次 / 间隔 {{ diag.network.retry_interval }}s
        </p>
        <table class="list">
          <thead>
            <tr><th>本地文件</th><th>状态</th><th>大小</th><th>修改时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="f in diag.storage.files" :key="f.path">
              <td class="mono small">{{ f.path }}</td>
              <td><span :class="['status-tag', f.exists ? 'ok' : 'failed']">{{ f.exists ? '存在' : '缺失' }}</span></td>
              <td class="muted small">{{ f.size }}</td>
              <td class="muted small">{{ f.mtime || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </template>
      <p v-else class="muted small">正在检查…</p>
    </div>

    <div class="panel">
      <div class="row head-row">
        <h3>GitHub 令牌</h3>
        <div class="spacer"></div>
        <button :disabled="busy === 'token'" @click="checkToken">{{ busy === 'token' ? '校验中…' : '重新校验' }}</button>
      </div>
      <template v-if="gh">
        <p class="hint">
          <span :class="['status-tag', gh.ok ? 'ok' : 'failed']">{{ gh.ok ? '可用' : '不可用' }}</span>
          类型 {{ gh.kind || '—' }}；账号 {{ gh.login || '—' }}；已尝试 {{ gh.tried }}/{{ gh.token_count }} 个令牌
          （{{ gh.token_source === 'repo' ? '仓库专属' : gh.token_source === 'global' ? '全局' : '无' }}）
        </p>
        <p v-if="gh.reason" class="hint">{{ gh.reason }}</p>
        <ul v-if="(gh.hints || []).length" class="hint-list">
          <li v-for="(h, i) in gh.hints" :key="i">{{ h }}</li>
        </ul>
        <p v-if="gh.repo" class="hint">
          仓库权限：推送 {{ gh.repo.can_push ? '允许' : '不允许' }} / 拉取 {{ gh.repo.can_pull ? '允许' : '不允许' }}
        </p>
      </template>
    </div>

    <div class="panel">
      <div class="row head-row">
        <h3>GitHub 延迟</h3>
        <div class="spacer"></div>
        <button :disabled="busy === 'latency'" @click="checkLatency">
          {{ busy === 'latency' ? '探测中…' : '重新探测' }}
        </button>
      </div>
      <div class="row" v-if="latency">
        <span :class="['status-tag', latClass(latency.api)]">API {{ latency.api.ok ? latency.api.ms + ' ms' : '不通' }}</span>
        <span :class="['status-tag', latClass(latency.git)]">Git {{ latency.git.ok ? latency.git.ms + ' ms' : '不通' }}</span>
      </div>
      <p v-else class="muted small">正在探测…</p>
    </div>

    <div class="panel">
      <h3>服务端凭据</h3>
      <p class="hint">GitHub Token 与 LLM 配置存于服务端（加密），本机只保留同步缓存。</p>
      <button :disabled="busy === 'sync'" @click="syncCredentials">
        {{ busy === 'sync' ? '同步中…' : '立即同步到本机缓存' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.panel h3,
.head-row h3 {
  margin: 0;
  font-size: 14px;
}

.panel {
  margin-bottom: 16px;
}

.head-row {
  margin-bottom: 10px;
}

.wrap {
  flex-wrap: wrap;
}

.small {
  font-size: 12px;
}

.hint-list {
  margin: 6px 0 0 18px;
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.7;
}

table.list {
  margin-top: 10px;
}
</style>
