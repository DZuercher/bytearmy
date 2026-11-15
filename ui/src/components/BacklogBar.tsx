import { ChevronLeft, ChevronRight, Plus } from 'lucide-react'
import { useState } from 'react'
import type { Project, TaskCreate } from '@/api/client'
import { BarHeading } from '@/components/BarHeading'
import { CreateTaskDialog } from '@/components/CreateTaskDialog'
import type { Doomed } from '@/components/DeleteDialog'
import { SortableItem, SortableList } from '@/components/Sortable'
import { TaskItem } from '@/components/TaskItem'
import { Button } from '@/components/ui/button'

type Props = {
  open: boolean
  onToggle: () => void
  project: Project | undefined // the focused project
  onCreate: (task: TaskCreate, start: boolean) => Promise<void>
  onStart: (slug: string) => void
  onCloneAgain: (slug: string) => void
  onEdit: (slug: string) => void
  onDelete: (doomed: Doomed) => void
  onReorder: (order: string[]) => void
}

// Right-hand bar: the tasks of the focused project that have not been started.
export function BacklogBar({
  open,
  onToggle,
  project,
  onCreate,
  onStart,
  onCloneAgain,
  onEdit,
  onDelete,
  onReorder,
}: Props) {
  const [creating, setCreating] = useState(false)

  if (!open) {
    return (
      <nav aria-label="Backlog tasks (collapsed)" className="flex w-12 shrink-0 flex-col items-center gap-3 border-l py-3">
        <Button variant="ghost" size="icon-sm" aria-label="Expand backlog bar" onClick={onToggle}>
          <ChevronLeft />
        </Button>
        <BarHeading vertical>Backlog tasks</BarHeading>
      </nav>
    )
  }

  return (
    <nav aria-label="Backlog tasks" className="flex w-56 shrink-0 flex-col gap-2 border-l p-4">
      <div className="flex items-center gap-1">
        <Button variant="ghost" size="icon-sm" aria-label="Collapse backlog bar" onClick={onToggle}>
          <ChevronRight />
        </Button>
        <div className="grow">
          <BarHeading>Backlog tasks</BarHeading>
        </div>
        {project && (
          <Button variant="ghost" size="icon-sm" aria-label="Add backlog task" onClick={() => setCreating(true)}>
            <Plus />
          </Button>
        )}
      </div>

      {project && (
        <>
          <SortableList
            ids={project.backlog.map((task) => task.slug)}
            onReorder={onReorder}
            className="flex min-h-0 flex-col gap-1 overflow-x-hidden overflow-y-auto"
          >
            {project.backlog.map((task) => (
              <SortableItem key={task.slug} id={task.slug}>
                {(handle, isDragging) => (
                  <TaskItem
                    task={task}
                    project={project}
                    handle={handle}
                    isDragging={isDragging}
                    onClick={() => onStart(task.slug)}
                    onCloneAgain={() => onCloneAgain(task.slug)}
                    onEdit={() => onEdit(task.slug)}
                    onDelete={onDelete}
                  >
                    {task.blocked_by.length > 0 && (
                      <span className="text-xs">Waits for {task.blocked_by.join(', ')}</span>
                    )}
                  </TaskItem>
                )}
              </SortableItem>
            ))}
          </SortableList>
          {project.backlog.length === 0 && <p className="text-muted-foreground">No backlog tasks.</p>}
          <CreateTaskDialog project={project} open={creating} onOpenChange={setCreating} onCreate={onCreate} />
        </>
      )}
    </nav>
  )
}
