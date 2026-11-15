import { type FormEvent, useState } from 'react'
import type { Project, TaskCreate } from '@/api/client'
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

const EMPTY = { name: '', description: '', branch: '', reminder: '', dependencies: [] as string[] }

export function CreateTaskDialog({
  project,
  open,
  onOpenChange,
  onCreate,
}: {
  project: Project
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreate: (task: TaskCreate, start: boolean) => Promise<void>
}) {
  const [task, setTask] = useState(EMPTY)
  const [error, setError] = useState<string | null>(null)
  const others = [...project.backlog, ...project.tasks]

  function close() {
    setTask(EMPTY)
    setError(null)
    onOpenChange(false)
  }

  // A task waiting for one in the backlog cannot start yet.
  const blocked = project.backlog.some((other) => task.dependencies.includes(other.slug))

  function create(start: boolean) {
    onCreate({ ...task, reminder: task.reminder || null }, start).then(close, (reason: unknown) =>
      setError(reason instanceof Error ? reason.message : String(reason)),
    )
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    create(false)
  }

  function depend(slug: string, checked: boolean) {
    const dependencies = task.dependencies.filter((other) => other !== slug)
    setTask({ ...task, dependencies: checked ? [...dependencies, slug] : dependencies })
  }

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? onOpenChange(true) : close())}>
      <DialogContent>
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>New task in {project.name}</DialogTitle>
            <DialogDescription>Only the name is required. The task goes to the backlog, or starts at once with its master agent working on the description.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="task-name">Name</Label>
            <Input
              id="task-name"
              autoFocus
              value={task.name}
              onChange={(event) => setTask({ ...task, name: event.target.value })}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="task-description">Description</Label>
            <Textarea
              id="task-description"
              value={task.description}
              onChange={(event) => setTask({ ...task, description: event.target.value })}
            />
          </div>
          {project.github_url && (
            <div className="grid gap-2">
              <Label htmlFor="task-branch">Branch</Label>
              <Input
                id="task-branch"
                placeholder="checked out if it exists, created otherwise"
                value={task.branch}
                onChange={(event) => setTask({ ...task, branch: event.target.value })}
              />
            </div>
          )}
          {others.length > 0 && (
            <fieldset className="grid gap-2">
              <legend className="mb-2 text-sm font-medium">Depends on</legend>
              <div className="grid max-h-32 gap-2 overflow-y-auto">
                {others.map((other) => (
                  <Label key={other.slug} className="font-normal">
                    <Checkbox
                      checked={task.dependencies.includes(other.slug)}
                      onCheckedChange={(checked) => depend(other.slug, checked === true)}
                    />
                    {other.name}
                  </Label>
                ))}
              </div>
            </fieldset>
          )}
          <div className="grid gap-2">
            <Label htmlFor="task-reminder">Reminder date</Label>
            <Input
              id="task-reminder"
              type="date"
              value={task.reminder}
              onChange={(event) => setTask({ ...task, reminder: event.target.value })}
            />
          </div>
          {error && <p className="text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={close}>
              Cancel
            </Button>
            <Button type="submit" variant="outline" disabled={task.name.trim() === ''}>
              Create task
            </Button>
            <Button
              type="button"
              disabled={task.name.trim() === '' || blocked}
              title={
                blocked
                  ? 'It depends on a task in the backlog'
                  : 'Start the task and its master agent, which gets the description'
              }
              onClick={() => create(true)}
            >
              Create and start
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
