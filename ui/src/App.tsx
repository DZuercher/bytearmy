import { useCallback, useEffect, useRef, useState } from 'react'
import { type AppState, type Bar, type Tree, type UiChange, api, listenForChanges } from '@/api/client'
import { BacklogBar } from '@/components/BacklogBar'
import { CreateProjectDialog } from '@/components/CreateProjectDialog'
import { CreateTaskDialog } from '@/components/CreateTaskDialog'
import { DeleteDialog, type Doomed } from '@/components/DeleteDialog'
import { EditTaskDialog } from '@/components/EditTaskDialog'
import { FocusTab } from '@/components/FocusTab'
import { InboxTab } from '@/components/InboxTab'
import { InboxView } from '@/components/InboxView'
import { OrchestratorTab } from '@/components/OrchestratorTab'
import { OrchestratorView } from '@/components/OrchestratorView'
import { Palette } from '@/components/Palette'
import { ProjectBar } from '@/components/ProjectBar'
import { ResizeHandle } from '@/components/ResizeHandle'
import { TaskBar } from '@/components/TaskBar'
import { Workspace } from '@/components/Workspace'

type BarWidth = 'left_width' | 'right_width' | 'bottom_width'

// Calls `call` for one item after the other, so the state the last call returns holds all
// of them. `items` must not be empty.
function inTurn(items: string[], call: (item: string) => Promise<AppState>): Promise<AppState> {
  return items.slice(1).reduce((before, item) => before.then(() => call(item)), call(items[0]))
}

const REFRESH_MS = 60_000 // a reminder becomes due without anyone clicking
const REFRESH_WHILE_CLONING_MS = 1_000

declare global {
  interface Window {
    // Provided by the Electron shell; absent when the page runs in a plain browser.
    // Each method takes a callback and returns the function that unsubscribes it.
    shell?: {
      onPaletteToggle: (callback: () => void) => () => void
      // A page in a web pane asked for a new window. `from` is the id of that pane's
      // browser view (`getWebContentsId()`), `url` the page the window would show.
      onOpenWindow: (callback: (request: { from: number; url: string }) => void) => () => void
    }
  }
}

export function App() {
  const [state, setState] = useState<AppState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [doomed, setDoomed] = useState<Doomed | null>(null)
  const [editing, setEditing] = useState<string | null>(null) // the slug of the task being edited
  // The creation dialog that is open. It covers only the main area, so the bars stay usable;
  // a task is created in the project the dialog was opened for, even if another one is focused since.
  const [creating, setCreating] = useState<{ kind: 'project' } | { kind: 'task'; project: string } | null>(null)
  const [paletteOpen, setPaletteOpen] = useState(false)
  // Workspaces stay mounted once opened, so their terminals keep their content.
  const [opened, setOpened] = useState<string[]>([])
  // The orchestrator's terminal stays mounted once shown, so it keeps its content.
  const [orchestratorOpened, setOrchestratorOpened] = useState(false)
  const activePanes = useRef<Record<string, string>>({})
  // The width of a bar whose edge is being dragged; the core keeps it once the edge is let go.
  const [drafts, setDrafts] = useState<Partial<Record<BarWidth, number>>>({})

  // Every call answers with the complete state: show it, or show why the call failed.
  const run = useCallback((call: Promise<AppState>) => {
    call.then(setState, (reason: unknown) =>
      setError(reason instanceof Error ? reason.message : String(reason)),
    )
  }, [])

  useEffect(() => run(api.state()), [run])
  useEffect(() => window.shell?.onPaletteToggle(() => setPaletteOpen((open) => !open)), [])
  // What the agents in the panes change shows at once.
  useEffect(() => listenForChanges(() => run(api.state())), [run])

  // The colors of the color scheme in use; the user can edit its file while bytearmy runs.
  // Every fetch brings a new object: compared as text, the colors are set only when they change.
  const themeText = state ? JSON.stringify(state.theme) : null
  useEffect(() => {
    if (!themeText) return
    const theme: AppState['theme'] = JSON.parse(themeText)
    const root = document.documentElement
    for (const [name, value] of Object.entries(theme.colors)) root.style.setProperty(`--${name}`, value)
    root.classList.toggle('dark', theme.appearance === 'dark')
    root.style.colorScheme = theme.appearance
    return () => {
      for (const name of Object.keys(theme.colors)) root.style.removeProperty(`--${name}`)
    }
  }, [themeText])

  const cloning =
    state?.projects.some((project) =>
      [...project.backlog, ...project.tasks].some((task) => task.clone === 'cloning'),
    ) ?? false
  useEffect(() => {
    const timer = setInterval(
      () => run(api.state()),
      cloning ? REFRESH_WHILE_CLONING_MS : REFRESH_MS,
    )
    return () => clearInterval(timer)
  }, [cloning, run])

  const project = state?.projects.find((candidate) => candidate.slug === state.focused_project)
  const task = project?.tasks.find((candidate) => candidate.slug === project.focused_task)
  const focused = project && task ? `${project.slug}/${task.slug}` : null
  const edited = project && [...project.backlog, ...project.tasks].find((candidate) => candidate.slug === editing)
  const creatingIn =
    creating?.kind === 'task' ? state?.projects.find((candidate) => candidate.slug === creating.project) : undefined
  useEffect(() => {
    if (focused) setOpened((keys) => (keys.includes(focused) ? keys : [...keys, focused]))
  }, [focused])
  const orchestratorView = state?.ui.view === 'orchestrator'
  useEffect(() => {
    if (orchestratorView) setOrchestratorOpened(true)
  }, [orchestratorView])

  if (state === null) return error && <p className="p-4 text-destructive">{error}</p>

  const { ui, projects } = state
  const setUi = (change: UiChange) => run(api.updateUi(change))
  const inboxShown = ui.view === 'inbox'
  const orchestratorShown = ui.view === 'orchestrator'
  const workspaceShown = ui.view === 'workspace'
  // In focus mode every bar shows collapsed; the bars' own flags keep what shows after it.
  const leftOpen = ui.left_open && !ui.focus
  const rightOpen = ui.right_open && !ui.focus
  const bottomOpen = ui.bottom_open && !ui.focus
  // Expanding a bar in focus mode ends it, with only that bar open.
  const toggleBar = (bar: 'left_open' | 'right_open' | 'bottom_open') =>
    setUi(
      ui.focus
        ? { focus: false, left_open: false, right_open: false, bottom_open: false, [bar]: true }
        : { [bar]: !ui[bar] },
    )
  const widthOf = (bar: BarWidth) => drafts[bar] ?? ui[bar]
  // The edge of an open bar, to drag it wider or narrower.
  const resizer = (bar: BarWidth, side: 'left' | 'right', label: string) => (
    <ResizeHandle
      label={label}
      side={side}
      width={widthOf(bar)}
      onResize={(width) => setDrafts((current) => ({ ...current, [bar]: width }))}
      onCommit={(width) => {
        setDrafts((current) => ({ ...current, [bar]: width }))
        // The draft goes together with the answer of the core, so the bar does not jump back.
        const forget = () =>
          setDrafts((current) => {
            const { [bar]: _, ...rest } = current
            return rest
          })
        api.updateUi({ [bar]: width }).then(
          (answer) => {
            setState(answer)
            forget()
          },
          (reason: unknown) => {
            setError(reason instanceof Error ? reason.message : String(reason))
            forget()
          },
        )
      }}
    />
  )
  const unread = state.inbox.messages.filter((message) => message.unread && !message.dismissed).length
  // Choosing a project, a task or a pane leaves the inbox and the orchestrator.
  const showWorkspace = () => {
    if (!workspaceShown) setUi({ view: 'workspace' })
  }

  function reorderProjects(order: string[]) {
    // Show the new order at once; the answer of the core replaces it.
    const bySlug = new Map(projects.map((candidate) => [candidate.slug, candidate]))
    setState({ ...state!, projects: order.map((slug) => bySlug.get(slug)!) })
    run(api.reorderProjects(order))
  }

  function reorderTasks(bar: Bar, order: string[]) {
    if (!project) return
    const key = bar === 'backlog' ? 'backlog' : 'tasks'
    const bySlug = new Map(project[key].map((candidate) => [candidate.slug, candidate]))
    const reordered = { ...project, [key]: order.map((slug) => bySlug.get(slug)!) }
    setState({
      ...state!,
      projects: projects.map((candidate) => (candidate === project ? reordered : candidate)),
    })
    run(api.reorderTasks(project.slug, bar, order))
  }

  function arrange(projectSlug: string, taskSlug: string, tree: Tree) {
    // While the user drags, the layout follows at once; the core hears of it on release.
    setState({
      ...state!,
      projects: projects.map((candidate) =>
        candidate.slug !== projectSlug
          ? candidate
          : {
              ...candidate,
              tasks: candidate.tasks.map((other) =>
                other.slug !== taskSlug
                  ? other
                  : { ...other, workspace: { ...other.workspace, tree: tree as typeof other.workspace.tree } },
              ),
            },
      ),
    })
  }

  function confirmDelete(confirmed: Doomed) {
    setDoomed(null)
    if (confirmed.kind === 'project') run(api.deleteProject(confirmed.slug))
    else if (project) run(api.deleteTask(project.slug, confirmed.slug))
  }

  const workspaces = projects.flatMap((candidate) =>
    candidate.tasks
      .filter((other) => opened.includes(`${candidate.slug}/${other.slug}`))
      .map((other) => ({ project: candidate, task: other, key: `${candidate.slug}/${other.slug}` })),
  )

  return (
    <div className="flex h-full flex-col bg-shell">
      {error && (
        <button type="button" className="bg-destructive px-4 py-1 text-left text-white" onClick={() => setError(null)}>
          {error} (click to dismiss)
        </button>
      )}
      {state.config_errors.length > 0 && (
        <div role="status" aria-label="Config problems" className="border-l-4 border-l-task-amber-mark bg-task-amber px-4 py-1 text-sm">
          {state.config_errors.map((problem) => (
            <div key={problem}>{problem}</div>
          ))}
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        <div className="relative flex shrink-0 flex-col" style={leftOpen ? { width: widthOf('left_width') } : undefined}>
          {leftOpen && resizer('left_width', 'right', 'Resize projects bar')}
          <OrchestratorTab
            open={leftOpen}
            active={orchestratorShown}
            running={state.orchestrator.running}
            onClick={() => setUi({ view: 'orchestrator' })}
          />
          <InboxTab
            open={leftOpen}
            active={inboxShown}
            unread={ui.focus ? 0 : unread}
            onClick={() => setUi({ view: 'inbox' })}
          />
          <ProjectBar
            open={leftOpen}
            onToggle={() => toggleBar('left_open')}
            projects={projects}
            focused={workspaceShown ? state.focused_project : null}
            onFocus={(slug) => {
              showWorkspace()
              run(api.focusProject(slug))
            }}
            onAdd={() => setCreating({ kind: 'project' })}
            onEdit={(slug, edit) => api.editProject(slug, edit).then(setState)}
            onDelete={setDoomed}
            onReorder={reorderProjects}
          />
        </div>
        <div className="relative flex shrink-0" style={bottomOpen ? { width: widthOf('bottom_width') } : undefined}>
          {bottomOpen && resizer('bottom_width', 'right', 'Resize tasks bar')}
          <TaskBar
            open={bottomOpen}
            onToggle={() => toggleBar('bottom_open')}
            project={project}
            onFocus={(slug) => {
              showWorkspace()
              run(api.focusTask(project!.slug, slug))
            }}
            onCloneAgain={(slug) => run(api.cloneAgain(project!.slug, slug))}
            onEdit={setEditing}
            onDelete={setDoomed}
            onReorder={(order) => reorderTasks('tasks', order)}
            onListHidden={(slug, open) => run(api.listHidden(project!.slug, slug, open))}
            onShowPane={(slug, pane) => {
              // The pane goes beside the one last used in its task, and that task is shown.
              showWorkspace()
              if (slug !== project!.focused_task) run(api.focusTask(project!.slug, slug))
              const beside = activePanes.current[`${project!.slug}/${slug}`] ?? null
              run(api.hidePane(project!.slug, slug, pane, false, beside))
            }}
          />
        </div>
        <main className="relative isolate flex min-w-0 flex-1 flex-col bg-background">
          <div className="flex items-baseline gap-3 px-4 pt-4 pb-3">
            <h1 className="text-lg font-semibold">
              {inboxShown ? 'Inbox' : orchestratorShown ? 'Orchestrator' : 'Workspace'}
            </h1>
            <p className="text-muted-foreground">
              {orchestratorShown
                ? state.orchestrator.running
                  ? 'Creates, starts and closes tasks, and reads the inbox.'
                  : 'Not running.'
                : inboxShown
                ? unread
                  ? `${unread} unread`
                  : 'Everything read.'
                : task && project
                ? `${project.name} / ${task.name}`
                : project
                  ? `${project.name}: no task is open.`
                  : 'Create or select a project.'}
            </p>
          </div>
          {workspaces.map((shown) => (
            <Workspace
              key={shown.key}
              project={shown.project}
              task={shown.task}
              visible={workspaceShown && shown.key === focused}
              run={run}
              onArrange={(tree) => arrange(shown.project.slug, shown.task.slug, tree)}
              onActivePane={(pane) => {
                activePanes.current[shown.key] = pane
              }}
            />
          ))}
          {orchestratorOpened && (
            <OrchestratorView
              orchestrator={state.orchestrator}
              visible={orchestratorShown}
              onEnded={() => run(api.state())}
              onRestart={() => run(api.restartOrchestrator())}
            />
          )}
          {inboxShown && (
            <InboxView
              inbox={state.inbox}
              onRead={(message, unread) => run(api.markMessage(message, unread))}
              onDismiss={(message) => run(api.dismissMessage(message))}
              onReadAll={(messages) => run(inTurn(messages, (message) => api.markMessage(message, false)))}
              onDismissAll={(messages) => run(inTurn(messages, api.dismissMessage))}
              onRefresh={() => run(api.refreshInbox())}
            />
          )}
          {creating?.kind === 'project' && (
            <CreateProjectDialog
              onClose={() => setCreating(null)}
              onCreate={(meta) => api.createProject(meta).then(setState)}
            />
          )}
          {creatingIn && (
            <CreateTaskDialog
              key={creatingIn.slug}
              project={creatingIn}
              onClose={() => setCreating(null)}
              onCreate={(created, start) => api.createTask(creatingIn.slug, created, start).then(setState)}
            />
          )}
        </main>
        <div className="relative flex shrink-0 flex-col" style={rightOpen ? { width: widthOf('right_width') } : undefined}>
          {rightOpen && resizer('right_width', 'left', 'Resize backlog bar')}
          <FocusTab open={rightOpen} active={ui.focus} onClick={() => setUi({ focus: !ui.focus })} />
          <BacklogBar
            open={rightOpen}
            onToggle={() => toggleBar('right_open')}
            project={project}
            onAdd={() => setCreating({ kind: 'task', project: project!.slug })}
            onStart={(slug) => run(api.startTask(project!.slug, slug))}
            onCloneAgain={(slug) => run(api.cloneAgain(project!.slug, slug))}
            onEdit={setEditing}
            onDelete={setDoomed}
            onReorder={(order) => reorderTasks('backlog', order)}
          />
        </div>
      </div>
      <DeleteDialog doomed={doomed} onCancel={() => setDoomed(null)} onConfirm={confirmDelete} />
      {project && edited && (
        <EditTaskDialog
          key={`${project.slug}/${edited.slug}`}
          task={edited}
          project={project}
          onClose={() => setEditing(null)}
          onSave={(edit) => api.editTask(project.slug, edited.slug, edit).then(setState)}
        />
      )}
      {paletteOpen && project && task && focused && (
        <Palette
          tools={state.tools ?? []}
          onClose={() => setPaletteOpen(false)}
          onRun={(tool, values) => {
            setPaletteOpen(false)
            run(
              api.openPane(project.slug, task.slug, {
                tool,
                values,
                beside: activePanes.current[focused] ?? null,
                hidden: false,
              }),
            )
          }}
        />
      )}
    </div>
  )
}
