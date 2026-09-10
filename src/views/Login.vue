<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { login, register, state, loadStatus, isLoggedIn } from '../store'
import { postJSON } from '../api'

const router = useRouter()
const mode = ref<'login' | 'register'>('login')
const email = ref('')
const password = ref('')
const confirm = ref('')
const busy = ref(false)
const errMsg = ref('')
const okMsg = ref('')

const remoteOpen = ref(false)
const remoteUrl = ref('')
const savingRemote = ref(false)

onMounted(async () => {
  if (!state.loaded) await loadStatus()
  if (isLoggedIn()) router.replace('/console')
  remoteUrl.value = state.status?.remote || ''
})

async function submit() {
  errMsg.value = ''
  okMsg.value = ''
  if (!email.value || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.value)) {
    errMsg.value = '请输入有效邮箱'
    return
  }
  if (password.value.length < 6) {
    errMsg.value = '密码至少 6 位'
    return
  }
  if (mode.value === 'register' && password.value !== confirm.value) {
    errMsg.value = '两次输入的密码不一致'
    return
  }
  busy.value = true
  try {
    if (mode.value === 'register') {
      await register(email.value, password.value)
      okMsg.value = '注册成功，已自动登录'
    } else {
      await login(email.value, password.value)
    }
    setTimeout(() => router.replace('/console'), 300)
  } catch (e: any) {
    errMsg.value = e?.message || '操作失败'
  } finally {
    busy.value = false
  }
}

async function saveRemote() {
  savingRemote.value = true
  errMsg.value = ''
  try {
    const j = await postJSON<{ remote: string }>('/api/remote/set', { remote: remoteUrl.value })
    remoteUrl.value = j.remote
    state.status && (state.status.remote = j.remote)
    okMsg.value = '远端服务地址已更新'
  } catch (e: any) {
    errMsg.value = e?.message || '保存失败'
  } finally {
    savingRemote.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <div class="panel login-card">
      <h2>{{ mode === 'login' ? '登录' : '注册' }}</h2>
      <p class="muted">账号数据以远端服务为准，登录凭证保存在本机 ~/.CodeVoyage</p>

      <div class="tabs">
        <button :class="mode === 'login' ? 'active' : ''" @click="mode = 'login'">登录</button>
        <button :class="mode === 'register' ? 'active' : ''" @click="mode = 'register'">注册</button>
      </div>

      <label>邮箱</label>
      <input v-model.trim="email" type="email" placeholder="you@example.com" autocomplete="email" />

      <label>密码</label>
      <input v-model="password" type="password" placeholder="至少 6 位" autocomplete="current-password" />

      <label v-if="mode === 'register'">确认密码</label>
      <input v-if="mode === 'register'" v-model="confirm" type="password" placeholder="再次输入密码" />

      <div v-if="errMsg" class="alert">{{ errMsg }}</div>
      <div v-if="okMsg" class="ok">{{ okMsg }}</div>

      <button class="primary full" :disabled="busy" @click="submit">
        {{ busy ? '处理中…' : mode === 'login' ? '登录' : '注册并登录' }}
      </button>

      <div class="remote-box">
        <button class="link" @click="remoteOpen = !remoteOpen">远端服务设置（当前：{{ remoteUrl || '未设置' }}）</button>
        <div v-if="remoteOpen" class="remote-inner">
          <input v-model.trim="remoteUrl" placeholder="https://CodeVoyage.yjlt.top" />
          <button :disabled="savingRemote" @click="saveRemote">保存</button>
          <p class="hint">本地开发可将远端指向本机：http://127.0.0.1:5000</p>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-wrap {
  min-height: calc(100vh - 120px);
  display: flex;
  align-items: center;
  justify-content: center;
}

.login-card {
  width: 380px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.tabs {
  display: flex;
  gap: 8px;
}

.tabs button {
  flex: 1;
}

.tabs button.active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

label {
  color: var(--text-dim);
  font-size: 13px;
  margin-top: 4px;
}

button.full {
  margin-top: 6px;
  width: 100%;
}

.ok {
  color: var(--ok);
  font-size: 13px;
}

.remote-box {
  margin-top: 14px;
  border-top: 1px dashed var(--border);
  padding-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

button.link {
  background: none;
  border: none;
  padding: 0;
  color: var(--accent);
  text-align: left;
  font-size: 12px;
}

.remote-inner {
  display: flex;
  gap: 8px;
}
</style>
