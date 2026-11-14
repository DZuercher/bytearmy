// Client for the Python core. Types come from the core's OpenAPI schema
// (`pixi run ui-types`), so a change on either side fails the type check.
import type { components, paths } from './schema'

type Schemas = components['schemas']
export type AppState = Schemas['AppState']
export type Project = Schemas['Project']
export type ProjectMeta = Schemas['ProjectMeta']
export type UiChange = Schemas['UiChange']

const routes = {
  state: '/api/state',
  projects: '/api/projects',
  project: '/api/projects/{slug}',
  order: '/api/projects/order',
  focus: '/api/focus',
  ui: '/api/ui',
} as const satisfies Record<string, keyof paths>

// The core serves this page and passes the token in the page's URL.
const token = new URLSearchParams(location.search).get('token') ?? ''

// Calls run one after the other, so their answers arrive in the order they were made
// and the last answer shown is the newest state.
let queue: Promise<unknown> = Promise.resolve()

function request(method: string, path: string, body?: unknown): Promise<AppState> {
  const answer = queue.then(() => send(method, path, body))
  queue = answer.catch(() => undefined)
  return answer
}

async function send(method: string, path: string, body?: unknown): Promise<AppState> {
  const response = await fetch(path, {
    method,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    const detail = await response
      .json()
      .then((error) => (typeof error.detail === 'string' ? error.detail : JSON.stringify(error.detail)))
      .catch(() => response.statusText)
    throw new Error(detail)
  }
  return response.json()
}

export const api = {
  state: () => request('GET', routes.state),
  createProject: (meta: ProjectMeta) => request('POST', routes.projects, meta),
  deleteProject: (slug: string) =>
    request('DELETE', routes.project.replace('{slug}', encodeURIComponent(slug))),
  reorderProjects: (order: string[]) =>
    request('PUT', routes.order, { order } satisfies Schemas['ProjectOrder']),
  focusProject: (slug: string | null) =>
    request('PUT', routes.focus, { slug } satisfies Schemas['Focus']),
  updateUi: (change: UiChange) => request('PATCH', routes.ui, change),
}
