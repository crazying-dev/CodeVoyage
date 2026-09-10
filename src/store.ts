import { reactive } from 'vue'
import { getJSON, postJSON } from './api'

export interface UserStatus {
  message: string
  logged_in: boolean
  email: string
  remote: string
  storage_dir: string
  storage_source: string
  github_configured: boolean
  github_tokens?: string[]
  github_token_count?: number
  github_token_hint: string
  llm_configured: boolean
  llm_api_key_hint: string
  llm_base_url: string
  llm_model: string
  git_name: string
  git_email: string
}

export const state = reactive<{
  status: UserStatus | null
  loaded: boolean
  error: string
}>({
  status: null,
  loaded: false,
  error: '',
})

/** 控制台当前页签（跨组件共享，便于从其它页面跳转） */
export const ui = reactive<{ tab: string }>({ tab: 'overview' })

export async function loadStatus() {
  try {
    state.status = await getJSON<UserStatus>('/api/user/status')
    state.error = ''
  } catch (e: any) {
    state.error = e?.message || String(e)
  } finally {
    state.loaded = true
  }
}

export async function login(email: string, password: string) {
  state.error = ''
  try {
    await postJSON('/api/user/login', { email, password })
    await loadStatus()
  } catch (e: any) {
    state.error = e?.message || String(e)
    throw e
  }
}

export async function register(email: string, password: string) {
  state.error = ''
  try {
    await postJSON('/api/user/register', { email, password })
    await loadStatus()
  } catch (e: any) {
    state.error = e?.message || String(e)
    throw e
  }
}

export async function logout() {
  await postJSON('/api/user/logout', {})
  state.status = null
}

export function isLoggedIn() {
  return !!state.status?.logged_in
}
