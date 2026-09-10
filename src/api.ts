// 与本地 centre(5431) 通信的统一封装；开发时由 vite 代理 /api。

export interface ApiError extends Error {
  status?: number
}

export async function postJSON<T = any>(url: string, data?: unknown): Promise<T> {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data ?? {}),
  })
  const text = await resp.text()
  let body: any = {}
  try {
    body = text ? JSON.parse(text) : {}
  } catch {
    body = { message: text }
  }
  if (!resp.ok) {
    const err = new Error(body?.message || `HTTP ${resp.status}`) as ApiError
    err.status = resp.status
    throw err
  }
  return body as T
}

export async function getJSON<T = any>(url: string): Promise<T> {
  const resp = await fetch(url)
  const body = await resp.json().catch(() => ({}))
  if (!resp.ok) {
    const err = new Error(body?.message || `HTTP ${resp.status}`) as ApiError
    err.status = resp.status
    throw err
  }
  return body as T
}

export interface WorkflowFile {
  text: string
  filename: string
}

/** 拉取 workflow 文本（代理返回 text/plain，需要单独解析 Content-Disposition）。 */
export async function fetchWorkflow(bindingId: number | string): Promise<WorkflowFile> {
  const resp = await fetch('/api/repo/workflow', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: bindingId }),
  })
  if (!resp.ok) {
    const text = await resp.text()
    throw new Error(text || `HTTP ${resp.status}`)
  }
  const text = await resp.text()
  const disp = resp.headers.get('Content-Disposition') || ''
  const m = disp.match(/filename="?([^";]+)"?/i)
  return { text, filename: m ? m[1] : 'codevoyage-workflow.yml' }
}

/** 浏览器端下载文本文件。 */
export function downloadText(filename: string, text: string, mime = 'text/plain') {
  const blob = new Blob([text], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
