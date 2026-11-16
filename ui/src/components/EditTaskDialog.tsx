import { type FormEvent, useState } from 'react'
import type { Project, Task, TaskEdit } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { type State, STATES } from '@/lib/states'

// Changes what the user may change about a task. Shown while `task` is being edited.
export function EditTaskDialog({
  task,
  project,
  onClose,
  onSave,
}: {
  task: Task
  project: Project
  onClose: () => void
  onSave: (edit: TaskEdit) => Promise<void>
}) {
  const [edit, setEdit] = useState({
    name: task.name,
    description: task.description,
    branch: task.branch,
    status: task.status,
    state: task.state,
    reminder: task.reminder ?? '',
    dependencies: task.dependencies,
  })
  const [error, setError] = useState<string | null>(null)
  const others = [...project.backlog, ...project.tasks].filter((other) => other.slug !== task.slug)
  const inBacklog = project.backlog.some((other) => other.slug === task.slug)
  // A started task can take every state but backlog; merged only with a GitHub URL.
  const states = (Object.keys(STATES) as State[]).filter(
    (state) => state !== 'backlog' && (state !== 'merged' || project.github_url || task.state === 'merged'),
  )
  // A branch is fixed once it is set; until then it can be added, after the clone is done.
  const branchOpen = !task.branch && task.clone !== 'cloning'

  function submit(event: FormEvent) {
    event.preventDefault()
    onSave({ ...edit, reminder: edit.reminder || null }).then(onClose, (reason: unknown) =>
      setError(reason instanceof Error ? reason.message : String(reason)),
    )
  }

  function depend(slug: string, checked: boolean) {
    const dependencies = edit.dependencies.filter((other) => other !== slug)
    setEdit({ ...edit, dependencies: checked ? [...dependencies, slug] : dependencies })
  }

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>Edit task in {project.name}</DialogTitle>
            <DialogDescription>
              The directory keeps its name. {task.branch ? 'The branch cannot be changed.' : 'A branch, once added, cannot be changed.'}
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="edit-task-name">Name</Label>
            <Input
              id="edit-task-name"
              autoFocus
              value={edit.name}
              onChange={(event) => setEdit({ ...edit, name: event.target.value })}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="edit-task-description">Description</Label>
            <Textarea
              id="edit-task-description"
              value={edit.description}
              onChange={(event) => setEdit({ ...edit, description: event.target.value })}
            />
          </div>
          {project.github_url && (
            <div className="grid gap-2">
              <Label htmlFor="edit-task-branch">Branch</Label>
              <Input
                id="edit-task-branch"
                value={edit.branch}
                disabled={!branchOpen}
                placeholder={task.clone === 'cloning' ? 'available once the clone is done' : 'none'}
                onChange={(event) => setEdit({ ...edit, branch: event.target.value })}
              />
            </div>
          )}
          {!inBacklog && (
            <div className="grid gap-2">
              <Label htmlFor="edit-task-status">Status</Label>
              <Input
                id="edit-task-status"
                value={edit.status}
                onChange={(event) => setEdit({ ...edit, status: event.target.value })}
              />
            </div>
          )}
          {!inBacklog && (
            <div className="grid gap-2">
              <Label htmlFor="edit-task-state">State</Label>
              <select
                id="edit-task-state"
                className="h-8 w-full rounded-lg border border-input bg-transparent px-2.5 py-1 md:text-sm"
                value={edit.state}
                onChange={(event) => setEdit({ ...edit, state: event.target.value as State })}
              >
                {states.map((state) => (
                  <option key={state} value={state}>
                    {STATES[state].label}
                  </option>
                ))}
              </select>
            </div>
          )}
          {others.length > 0 && (
            <fieldset className="grid gap-2">
              <legend className="mb-2 text-sm font-medium">Depends on</legend>
              <div className="grid max-h-32 gap-2 overflow-y-auto">
                {others.map((other) => (
                  <Label key={other.slug} className="font-normal">
                    <Checkbox
                      checked={edit.dependencies.includes(other.slug)}
                      onCheckedChange={(checked) => depend(other.slug, checked === true)}
                    />
                    {other.name}
                  </Label>
                ))}
              </div>
            </fieldset>
          )}
          <div className="grid gap-2">
            <Label htmlFor="edit-task-reminder">Reminder date</Label>
            <Input
              id="edit-task-reminder"
              type="date"
              value={edit.reminder}
              onChange={(event) => setEdit({ ...edit, reminder: event.target.value })}
            />
          </div>
          {error && <p className="text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={edit.name.trim() === '' || edit.status.trim() === ''}>
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
