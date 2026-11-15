import { useCallback, useEffect, useRef, useState } from 'react'
import { type AppState, type Bar, type Tree, type UiChange, api } from '@/api/client'
import { BacklogBar } from '@/components/BacklogBar'
import { DeleteDialog, type Doomed } from '@/components/DeleteDialog'
import { Palette } from '@/components/Palette'
import { ProjectBar } from '@/components/ProjectBar'
import { TaskBar } from '@/components/TaskBar'
import { Workspace } from '@/components/Workspace'

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
  const [paletteOpen, setPaletteOpen] = useState(false)
  // Workspaces stay mounted once opened, so their terminals keep their content.
  const [opened, setOpened] = useState<string[]>([])
  const activePanes = useRef<Record<string, string>>({})

  // Every call answers with the complete state: show it, or show why the call failed.
  const run = useCallback((call: Promise<AppState>) => {
    call.then(setState, (reason: unknown) =>
      setError(reason instanceof Error ? reason.message : String(reason)),
    )
  }, [])

  useEffect(() => run(api.state()), [run])
  useEffect(() => window.shell?.onPaletteToggle(() => setPaletteOpen((open) => !open)), [])

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
  useEffect(() => {
    if (focused) setOpened((keys) => (keys.includes(focused) ? keys : [...keys, focused]))
  }, [focused])

  if (state === null) return error && <p className="p-4 text-destructive">{error}</p>

  const { ui, projects } = state
  const setUi = (change: UiChange) => run(api.updateUi(change))

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
    <div className="flex h-full flex-col bg-zinc-50">
      {error && (
        <button type="button" className="bg-destructive px-4 py-1 text-left text-white" onClick={() => setError(null)}>
          {error} (click to dismiss)
        </button>
      )}
      <div className="flex min-h-0 flex-1">
        <ProjectBar
          open={ui.left_open}
          onToggle={() => setUi({ left_open: !ui.left_open })}
          projects={projects}
          focused={state.focused_project}
          onFocus={(slug) => run(api.focusProject(slug))}
          onCreate={(meta) => api.createProject(meta).then(setState)}
          onDelete={setDoomed}
          onReorder={reorderProjects}
        />
        <main className="flex min-w-0 flex-1 flex-col bg-white">
          <div className="flex items-baseline gap-3 px-4 pt-4 pb-3">
            <h1 className="text-lg font-semibold">Workspace</h1>
            <p className="text-muted-foreground">
              {task && project
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
              visible={shown.key === focused}
              run={run}
              onArrange={(tree) => arrange(shown.project.slug, shown.task.slug, tree)}
              onActivePane={(pane) => {
                activePanes.current[shown.key] = pane
              }}
            />
          ))}
        </main>
        <BacklogBar
          open={ui.right_open}
          onToggle={() => setUi({ right_open: !ui.right_open })}
          project={project}
          onCreate={(created) => api.createTask(project!.slug, created).then(setState)}
          onStart={(slug) => run(api.startTask(project!.slug, slug))}
          onCloneAgain={(slug) => run(api.cloneAgain(project!.slug, slug))}
          onDelete={setDoomed}
          onReorder={(order) => reorderTasks('backlog', order)}
        />
      </div>
      <TaskBar
        open={ui.bottom_open}
        onToggle={() => setUi({ bottom_open: !ui.bottom_open })}
        project={project}
        onFocus={(slug) => run(api.focusTask(project!.slug, slug))}
        onCloneAgain={(slug) => run(api.cloneAgain(project!.slug, slug))}
        onDelete={setDoomed}
        onReorder={(order) => reorderTasks('tasks', order)}
      />
      <DeleteDialog doomed={doomed} onCancel={() => setDoomed(null)} onConfirm={confirmDelete} />
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
              }),
            )
          }}
        />
      )}
    </div>
  )
}
