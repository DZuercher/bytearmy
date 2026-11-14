// Client for the Python core. The core serves this page, so it is same-origin.
const token = new URLSearchParams(location.search).get('token') ?? ''
const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

export async function getState() {
  const response = await fetch('/state', { headers })
  if (!response.ok) throw new Error(`GET /state: HTTP ${response.status}`)
  return response.json()
}

export async function rpc(method, params = {}) {
  const response = await fetch('/rpc', {
    method: 'POST',
    headers,
    body: JSON.stringify({ method, params }),
  })
  const body = await response.json()
  if (!response.ok) throw new Error(`${method}: ${body.detail ?? response.status}`)
  return body.result
}

export function openSocket(path) {
  const origin = location.origin.replace(/^http/, 'ws')
  return new WebSocket(`${origin}${path}?token=${encodeURIComponent(token)}`)
}
