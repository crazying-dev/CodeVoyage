<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { postJSON, fetchWorkflow, downloadText } from '../api'

interface Repo {
  id: number
  owner: string
  name: string
  keywords: string[]
  authors: string[]
  issue_count: number
  created_at: string
}

const repos = ref<Repo[]>([])
const err = ref('')
const msg = ref('')

const owner = ref('')
const name = ref('')
const keywords = ref('')
const authors = ref('')

// 行内编辑：关键词 + 触发者白名单
const editMap = ref<Record<number, { keywords: string; authors: string }>>({})
const busyId = ref<number | null>(null)

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
  try {
    const j = await postJSON<{ repos: Repo[] }>('/api/repo/list', {})
    repos.value = j.repos
  } catch (e: any) {
    err.value = e?.message || String(e)
  }
}

async function bind() {
  err.value = ''
  msg.value = ''
  if (!owner.value.trim() || !name.value.trim()) {
    err.value = '请填写仓库作者与名称'
    return
  }
  try {
    await postJSON('/api/repo/bind', {
      owner: owner.value.trim(),
      name: name.value.trim(),
      keywords: splitList(keywords.value),
      authors: splitList(authors.value, true),
    })
    msg.value = '绑定成功（若该仓库已被其他账号绑定会失败）'
    owner.value = ''
    name.value = ''
    keywords.value = ''
    authors.value = ''
    await refresh()
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
  editMap.value[r.id] = { keywords: kwText(r), authors: authorsText(r) }
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
    delete editMap.value[r.id]
    await refresh()
  } catch (e: any) {
    err.value = e?.message || String(e)
  } finally {
    busyId.value = null
  }
}

async function unbind(r: Repo) {
  if (!confirm(`确定解绑 ${r.owner}/${r.name} 吗？解绑后该仓库可被其他账号绑定。`)) return
  err.value = ''
  try {
    await postJSON('/api/repo/unbind', { id: r.id })
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

onMounted(refresh)
</script>

<template>
  <div class="repos">
    <div v-if="err" class="alert">{{ err }}</div>
    <div v-if="msg" class="ok">{{ msg }}</div>

    <div class="panel form-panel">
      <h3>绑定新仓库</h3>
      <div class="grid4">
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
      </div>
      <p class="hint">
        每个仓库只能绑定一个账号。作者白名单为空时仅允许仓库 owner 触发；填写后仅白名单用户与 owner 可触发。
        workflow 生成与安装检查请到「Workflow 生成器」页。
      </p>
      <button class="primary" @click="bind">绑定</button>
    </div>

    <div class="panel">
      <h3>已绑定仓库</h3>
      <table v-if="repos.length" class="list">
        <thead>
          <tr>
            <th>仓库</th>
            <th>关键词</th>
            <th>触发者白名单</th>
            <th>任务数</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in repos" :key="r.id">
            <td class="mono">{{ r.owner }}/{{ r.name }}</td>
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
                  <button class="primary" :disabled="busyId === r.id" @click="saveEdit(r)">保存</button>
                  <button @click="cancelEdit(r)">取消</button>
                </template>
                <template v-else>
                  <button @click="startEdit(r)">编辑</button>
                  <button @click="downloadWorkflow(r)">下载 workflow</button>
                  <button class="danger" @click="unbind(r)">解绑</button>
                </template>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">尚未绑定任何仓库。</p>
    </div>
  </div>
</template>

<style scoped>
.ok {
  color: var(--ok);
  margin-bottom: 10px;
}

.form-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.grid4 {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}

label {
  display: block;
  color: var(--text-dim);
  font-size: 13px;
  margin-bottom: 6px;
}

.kw-input {
  min-width: 220px;
}

.wrap-actions {
  flex-wrap: wrap;
}
</style>
