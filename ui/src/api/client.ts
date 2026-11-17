// Client for the Python core. Types come from the core's OpenAPI schema
// (`pixi run ui-types`), so a change on either side fails the type check.
import type { components, paths } from './schema'

type Schemas = components['schemas']
export type AppState = Schemas['AppState']
export type Project = Schemas['Project']
export type ProjectMeta = Schemas['ProjectMeta']
export type ProjectEdit = Schemas['ProjectEdit']
export type Task = Schemas['Task']
export type TaskCreate = Schemas['TaskCreate']
export type TaskEdit = Schemas['TaskEdit']
export type UiChange = Schemas['UiChange']
export type Bar = Schemas['TaskOrder']['bar']
export type Pane = Schemas['Pane']
export type PaneOpen = Schemas['PaneOpen']
export type Tool = Schemas['Tool']
export type Workspace = Schemas['Workspace']
export type Tree = Schemas['Layout']['tree']
export type Message = Schemas['Message']
export type Inbox = Schemas['InboxState']
export type Orchestrator = Schemas['OrchestratorState']
export type SettingsFile = Schemas['SettingsFile']

const routes = {
  state: '/api/state',
  ui: '/api/ui',
  focus: '/api/focus',
  projects: '/api/projects',
  projectOrder: '/api/projects/order',
  project: '/api/projects/{project}',
  branches: '/api/projects/{project}/branches',
  tasks: '/api/projects/{project}/tasks',
  taskOrder: '/api/projects/{project}/tasks/order',
  taskFocus: '/api/projects/{project}/tasks/focus',
  taskStart: '/api/projects/{project}/tasks/{task}/start',
  taskClone: '/api/projects/{project}/tasks/{task}/clone',
  task: '/api/projects/{project}/tasks/{task}',
  layout: '/api/projects/{project}/tasks/{task}/layout',
  maximized: '/api/projects/{project}/tasks/{task}/maximized',
  hiddenOpen: '/api/projects/{project}/tasks/{task}/hidden-open',
  panes: '/api/projects/{project}/tasks/{task}/panes',
  pane: '/api/projects/{project}/tasks/{task}/panes/{pane}',
  paneFinish: '/api/projects/{project}/tasks/{task}/panes/{pane}/finish',
  paneHighlight: '/api/projects/{project}/tasks/{task}/panes/{pane}/highlight',
  paneHidden: '/api/projects/{project}/tasks/{task}/panes/{pane}/hidden',
  panePage: '/api/projects/{project}/tasks/{task}/panes/{pane}/page',
  panePageVersion: '/api/projects/{project}/tasks/{task}/panes/{pane}/page/version',
  inboxRefresh: '/api/inbox/refresh',
  inboxRead: '/api/inbox/{message}/read',
  inboxDismiss: '/api/inbox/{message}/dismiss',
  orchestratorRestart: '/api/orchestrator/restart',
  settings: '/api/settings',
  settingsFile: '/api/settings/{file}',
  projectSettings: '/api/projects/{project}/settings',
  projectSettingsFile: '/api/projects/{project}/settings/{file}',
} as const satisfies Record<string, keyof paths>

function fill(route: string, parts: { project: string; task?: string; pane?: string }): string {
  return route
    .replace('{project}', encodeURIComponent(parts.project))
    .replace('{task}', encodeURIComponent(parts.task ?? ''))
    .replace('{pane}', encodeURIComponent(parts.pane ?? ''))
}

// The core serves this page and passes the token in the page's URL.
const token = new URLSearchParams(location.search).get('token') ?? ''

// Calls run one after the other, so their answers arrive in the order they were made
// and the last answer shown is the newest state.
let queue: Promise<unknown> = Promise.resolve()

function request(method: string, path: string, body?: unknown): Promise<AppState> {
  const answer = queue.then(() => send<AppState>(method, path, body))
  queue = answer.catch(() => undefined)
  return answer
}

async function send<Answer>(method: string, path: string, body?: unknown): Promise<Answer> {
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
  editProject: (project: string, edit: ProjectEdit) =>
    request('PUT', fill(routes.project, { project }), edit),
  deleteProject: (project: string) => request('DELETE', fill(routes.project, { project })),
  reorderProjects: (order: string[]) =>
    request('PUT', routes.projectOrder, { order } satisfies Schemas['ProjectOrder']),
  focusProject: (slug: string | null) =>
    request('PUT', routes.focus, { slug } satisfies Schemas['Focus']),

  // With `start`, the task starts at once and its master agent gets the description.
  createTask: (project: string, task: TaskCreate, start = false) =>
    request('POST', fill(routes.tasks, { project }) + (start ? '?start=true' : ''), task),
  startTask: (project: string, task: string) =>
    request('POST', fill(routes.taskStart, { project, task })),
  cloneAgain: (project: string, task: string) =>
    request('POST', fill(routes.taskClone, { project, task })),
  editTask: (project: string, task: string, edit: TaskEdit) =>
    request('PUT', fill(routes.task, { project, task }), edit),
  deleteTask: (project: string, task: string) =>
    request('DELETE', fill(routes.task, { project, task })),
  reorderTasks: (project: string, bar: Bar, order: string[]) =>
    request('PUT', fill(routes.taskOrder, { project }), { bar, order } satisfies Schemas['TaskOrder']),
  focusTask: (project: string, slug: string | null) =>
    request('PUT', fill(routes.taskFocus, { project }), { slug } satisfies Schemas['Focus']),

  openPane: (project: string, task: string, pane: PaneOpen) =>
    request('POST', fill(routes.panes, { project, task }), pane),
  closePane: (project: string, task: string, pane: string) =>
    request('DELETE', fill(routes.pane, { project, task, pane })),
  finishReview: (project: string, task: string, pane: string) =>
    request('POST', fill(routes.paneFinish, { project, task, pane })),
  setLayout: (project: string, task: string, tree: Tree) =>
    request('PUT', fill(routes.layout, { project, task }), { tree } satisfies Schemas['Layout']),
  maximizePane: (project: string, task: string, slug: string | null) =>
    request('PUT', fill(routes.maximized, { project, task }), { slug } satisfies Schemas['Focus']),
  highlightPane: (project: string, task: string, pane: string, on: boolean) =>
    request('PUT', fill(routes.paneHighlight, { project, task, pane }), { on } satisfies Schemas['Highlight']),
  // A pane is shown beside `beside`, the last pane if that is null.
  hidePane: (project: string, task: string, pane: string, hidden: boolean, beside: string | null = null) =>
    request('PUT', fill(routes.paneHidden, { project, task, pane }), {
      hidden,
      beside,
    } satisfies Schemas['Visibility']),
  listHidden: (project: string, task: string, open: boolean) =>
    request('PUT', fill(routes.hiddenOpen, { project, task }), { open } satisfies Schemas['Unfold']),

  refreshInbox: () => request('POST', routes.inboxRefresh),
  markMessage: (message: string, unread: boolean) =>
    request('POST', routes.inboxRead.replace('{message}', encodeURIComponent(message)), {
      unread,
    } satisfies Schemas['ReadChange']),
  dismissMessage: (message: string) =>
    request('POST', routes.inboxDismiss.replace('{message}', encodeURIComponent(message))),

  restartOrchestrator: () => request('POST', routes.orchestratorRestart),

  editSettings: (file: string, text: string) =>
    request('PUT', routes.settingsFile.replace('{file}', encodeURIComponent(file)), {
      text,
    } satisfies Schemas['SettingsEdit']),
  editProjectSettings: (project: string, file: string, text: string) =>
    request('PUT', fill(routes.projectSettingsFile, { project }).replace('{file}', encodeURIComponent(file)), {
      text,
    } satisfies Schemas['SettingsEdit']),
}

// Calls `onChange` whenever the core's data changed, also by a program in a pane, so the
// window can fetch the state again. Returns the function that stops listening.
export function listenForChanges(onChange: () => void): () => void {
  let socket: WebSocket | null = null
  let retry: ReturnType<typeof setTimeout> | undefined
  let stopped = false
  const connect = () => {
    socket = new WebSocket(`${location.origin.replace(/^http/, 'ws')}/api/changes?token=${token}`)
    socket.onmessage = () => onChange()
    socket.onclose = () => {
      if (!stopped) retry = setTimeout(connect, 1_000) // the core is busy or restarting
    }
  }
  connect()
  return () => {
    stopped = true
    clearTimeout(retry)
    socket?.close()
  }
}

// The branches a new task can start from, the default branch first. Not queued: the
// answer is not the state, and it waits for a fetch of the repository.
export function branches(project: string): Promise<string[]> {
  return send<string[]>('GET', fill(routes.branches, { project }))
}

// The files the settings dialogue edits: those of the root, or of a project. Not queued:
// the answer is not the state.
export function settings(project?: string): Promise<SettingsFile[]> {
  return send<SettingsFile[]>('GET', project ? fill(routes.projectSettings, { project }) : routes.settings)
}

// The page of a web pane. It does not wait in the queue and holds nobody up: the answer
// is not the state, and it can take minutes while the service of a tool starts.
export function panePage(place: { project: string; task: string; pane: string }): Promise<string> {
  return send<Schemas['Page']>('GET', fill(routes.panePage, place)).then((page) => page.url)
}

// A text that changes when a file the web pane shows changes; null if it shows no file.
export function panePageVersion(place: {
  project: string
  task: string
  pane: string
}): Promise<string | null> {
  return send<Schemas['PageVersion']>('GET', fill(routes.panePageVersion, place)).then(
    (answer) => answer.version ?? null,
  )
}

// The terminal of a pane. WebSocket routes are not part of the schema, so this path is
// the one place the type check does not cover.
export type TerminalSize = { rows: number; cols: number }

function terminalSocket(path: string, size: TerminalSize): WebSocket {
  const query = new URLSearchParams({ token, rows: String(size.rows), cols: String(size.cols) })
  return new WebSocket(`${location.origin.replace(/^http/, 'ws')}${path}?${query}`)
}

export function openTerminal(
  place: { project: string; task: string; pane: string },
  size: TerminalSize,
): WebSocket {
  return terminalSocket(`${fill(routes.pane, place)}/terminal`, size)
}

// The terminal of the orchestrator, the one agent above all tasks.
export function openOrchestratorTerminal(size: TerminalSize): WebSocket {
  return terminalSocket('/api/orchestrator/terminal', size)
}
