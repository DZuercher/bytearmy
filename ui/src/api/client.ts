// Client for the Python core. Types come from the core's OpenAPI schema
// (`pixi run ui-types`), so a change on either side fails the type check.
import type { components, paths } from './schema'

type Schemas = components['schemas']
export type AppState = Schemas['AppState']
export type Project = Schemas['Project']
export type ProjectMeta = Schemas['ProjectMeta']
export type Task = Schemas['Task']
export type TaskCreate = Schemas['TaskCreate']
export type UiChange = Schemas['UiChange']
export type Bar = Schemas['TaskOrder']['bar']

const routes = {
  state: '/api/state',
  ui: '/api/ui',
  focus: '/api/focus',
  projects: '/api/projects',
  projectOrder: '/api/projects/order',
  project: '/api/projects/{project}',
  tasks: '/api/projects/{project}/tasks',
  taskOrder: '/api/projects/{project}/tasks/order',
  taskFocus: '/api/projects/{project}/tasks/focus',
  taskStart: '/api/projects/{project}/tasks/{task}/start',
  task: '/api/projects/{project}/tasks/{task}',
} as const satisfies Record<string, keyof paths>

function fill(route: string, parts: { project: string; task?: string }): string {
  return route
    .replace('{project}', encodeURIComponent(parts.project))
    .replace('{task}', encodeURIComponent(parts.task ?? ''))
}

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
  updateUi: (change: UiChange) => request('PATCH', routes.ui, change),

  createProject: (meta: ProjectMeta) => request('POST', routes.projects, meta),
  deleteProject: (project: string) => request('DELETE', fill(routes.project, { project })),
  reorderProjects: (order: string[]) =>
    request('PUT', routes.projectOrder, { order } satisfies Schemas['ProjectOrder']),
  focusProject: (slug: string | null) =>
    request('PUT', routes.focus, { slug } satisfies Schemas['Focus']),

  createTask: (project: string, task: TaskCreate) =>
    request('POST', fill(routes.tasks, { project }), task),
  startTask: (project: string, task: string) =>
    request('POST', fill(routes.taskStart, { project, task })),
  deleteTask: (project: string, task: string) =>
    request('DELETE', fill(routes.task, { project, task })),
  reorderTasks: (project: string, bar: Bar, order: string[]) =>
    request('PUT', fill(routes.taskOrder, { project }), { bar, order } satisfies Schemas['TaskOrder']),
  focusTask: (project: string, slug: string | null) =>
    request('PUT', fill(routes.taskFocus, { project }), { slug } satisfies Schemas['Focus']),
}
