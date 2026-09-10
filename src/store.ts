import { reactive } from 'vue'
import { getJSON, postJSON } from './api'

export interface UserStatus {
  message: string
  logged_in: boolean
  email: string
  remote: string
  github_configured: boolean
  llm_configured: boolean
  llm_model: string
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
