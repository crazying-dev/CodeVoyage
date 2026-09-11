<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { getJSON } from '../api'

const logs = ref('')
const err = ref('')
const auto = ref(true)
let timer: number | null = null

async function refresh() {
  try {
    err.value = ''
    const j = await getJSON<{ logs: string }>('/api/Agent/logs')
    logs.value = j.logs || '（暂无日志）'
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

function startTimer() {
  stopTimer()
  timer = window.setInterval(() => {
    if (auto.value) refresh()
  }, 3000)
}

function stopTimer() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

onMounted(() => {
  refresh()
  startTimer()
})

onUnmounted(stopTimer)
</script>

<template>
  <div class="logs">
    <div class="row toolbar">
      <h3>运行日志</h3>
      <div class="spacer"></div>
      <label class="auto"><input v-model="auto" type="checkbox" /> 自动刷新（3s）</label>
      <button @click="refresh">刷新</button>
    </div>
    <div v-if="err" class="alert">{{ err }}</div>
    <pre class="log">{{ logs }}</pre>
    <p class="hint">
      日志包含：收到任务、等待确认、克隆/AI/提交/推送、创建 PR、回执远端等关键步骤，便于排查「无效果」问题。
    </p>
  </div>
</template>

<style scoped>
.toolbar {
  margin-bottom: 10px;
}

h3 {
  margin: 0;
  font-size: 15px;
}

.log {
  max-height: 520px;
}

.auto {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-dim);
  font-size: 13px;
}

.auto input {
  width: auto;
}
</style>
