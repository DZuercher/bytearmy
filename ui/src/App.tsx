import { useCallback, useEffect, useState } from 'react'
import { type AppState, type UiChange, api } from '@/api/client'
import { BacklogBar } from '@/components/BacklogBar'
import { ProjectBar } from '@/components/ProjectBar'
import { TaskBar } from '@/components/TaskBar'

export function App() {
  const [state, setState] = useState<AppState | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Every call answers with the complete state: show it, or show why the call failed.
  const run = useCallback((call: Promise<AppState>) => {
    call.then(setState, (reason: unknown) => setError(String(reason)))
  }, [])

  useEffect(() => run(api.state()), [run])

  if (state === null) return error && <p className="p-4 text-destructive">{error}</p>

  const { ui, projects } = state
  const focused = projects.find((project) => project.slug === state.focused_project)
  const setUi = (change: UiChange) => run(api.updateUi(change))

  function reorder(order: string[]) {
    // Show the new order at once; the answer of the core replaces it.
    const bySlug = new Map(projects.map((project) => [project.slug, project]))
    setState({ ...state!, projects: order.map((slug) => bySlug.get(slug)!) })
    run(api.reorderProjects(order))
  }

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
          onDelete={(slug) => run(api.deleteProject(slug))}
          onReorder={reorder}
        />
        <main className="flex min-w-0 flex-1 flex-col gap-3 bg-white p-4">
          <h1 className="text-lg font-semibold">Workspace</h1>
          <p className="text-muted-foreground">
            {focused ? `${focused.name}: no task is open.` : 'Create or select a project.'}
          </p>
        </main>
        <BacklogBar open={ui.right_open} onToggle={() => setUi({ right_open: !ui.right_open })} />
      </div>
      <TaskBar open={ui.bottom_open} onToggle={() => setUi({ bottom_open: !ui.bottom_open })} />
    </div>
  )
}
