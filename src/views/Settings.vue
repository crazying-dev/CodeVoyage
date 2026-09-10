<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { postJSON } from '../api'
import { state, loadStatus } from '../store'

const err = ref('')
const ok = ref('')

const remoteUrl = ref('')
const savingRemote = ref(false)

const githubToken = ref('')
const llmKey = ref('')
const llmBase = ref('')
const llmModel = ref('')
const savingLocal = ref(false)

const githubSet = computed(() => !!state.status?.github_configured)
const llmSet = computed(() => !!state.status?.llm_configured)

onMounted(async () => {
  if (!state.loaded) await loadStatus()
  remoteUrl.value = state.status?.remote || ''
})

async function saveRemote() {
  err.value = ''
  ok.value = ''
  savingRemote.value = true
  try {
    const j = await postJSON<{ remote: string }>('/api/remote/set', { remote: remoteUrl.value })
    remoteUrl.value = j.remote
    if (state.status) state.status.remote = j.remote
    ok.value = '远端服务地址已保存'
  } catch (e: any) {
    err.value = e?.message || String(e)
  } finally {
    savingRemote.value = false
  }
}

async function saveLocal() {
  err.value = ''
  ok.value = ''
  const payload: Record<string, string> = {}
  if (githubToken.value !== '') payload.github_token = githubToken.value
  if (llmKey.value !== '') payload.llm_api_key = llmKey.value
  if (llmBase.value !== '') payload.llm_base_url = llmBase.value
  if (llmModel.value !== '') payload.llm_model = llmModel.value
  if (!Object.keys(payload).length) {
    err.value = '没有需要保存的内容'
    return
  }
  savingLocal.value = true
  try {
    const j = await postJSON<{ github_configured: boolean; llm_configured: boolean }>('/api/local/config', payload)
    ok.value = '本地配置已保存'
    githubToken.value = ''
    llmKey.value = ''
    llmBase.value = ''
    llmModel.value = ''
    if (state.status) {
      state.status.github_configured = j.github_configured
      state.status.llm_configured = j.llm_configured
    }
    await loadStatus()
  } catch (e: any) {
    err.value = e?.message || String(e)
  } finally {
    savingLocal.value = false
  }
}
</script>

<template>
  <div class="settings">
    <div v-if="err" class="alert">{{ err }}</div>
    <div v-if="ok" class="ok">{{ ok }}</div>

    <div class="panel">
      <h3>远端服务</h3>
      <p class="muted">账号、仓库绑定与任务记录均以远端为主。默认 https://CodeVoyage.yjlt.top</p>
      <div class="row input-row">
        <input v-model.trim="remoteUrl" placeholder="远端服务地址" />
        <button :disabled="savingRemote" @click="saveRemote">保存</button>
      </div>
      <p class="hint">本地联调可指向本机运行的远端：http://127.0.0.1:5000</p>
    </div>

    <div class="panel">
      <h3>本地配置（仅保存在本机 ~/.CodeVoyage，混淆落盘，绝不外传）</h3>

      <label>GitHub Token（PAT）</label>
      <div class="row input-row">
        <input v-model="githubToken" type="password" :placeholder="githubSet ? '已配置 · 留空保持不变' : 'ghp_... 需具备该仓库写权限'" />
      </div>

      <label>LLM API Key</label>
      <div class="row input-row">
        <input v-model="llmKey" type="password" :placeholder="llmSet ? '已配置 · 留空保持不变' : 'sk-...'" />
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
        Agent 执行任务需要：GitHub Token（克隆与推送）与 LLM Key（代码修改）。填入后对应状态会在「Agent 运行」页点亮。
      </p>
      <button class="primary" :disabled="savingLocal" @click="saveLocal">保存本地配置</button>
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
  max-width: 720px;
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

.grid2 {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}
</style>
