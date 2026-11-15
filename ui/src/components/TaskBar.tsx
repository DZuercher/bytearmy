import { ChevronLeft, ChevronRight } from 'lucide-react'
import type { Project } from '@/api/client'
import { BarHeading } from '@/components/BarHeading'
import type { Doomed } from '@/components/DeleteDialog'
import { SortableItem, SortableList } from '@/components/Sortable'
import { TaskItem } from '@/components/TaskItem'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

type Props = {
  open: boolean
  onToggle: () => void
  project: Project | undefined // the focused project
  onFocus: (slug: string) => void
  onCloneAgain: (slug: string) => void
  onDelete: (doomed: Doomed) => void
  onReorder: (order: string[]) => void
}

// Left-hand bar, right of the projects: the started tasks of the focused project.
export function TaskBar({ open, onToggle, project, onFocus, onCloneAgain, onDelete, onReorder }: Props) {
  if (!open) {
    return (
      <nav aria-label="Tasks (collapsed)" className="flex w-12 shrink-0 flex-col items-center gap-3 border-r py-3">
        <Button variant="ghost" size="icon-sm" aria-label="Expand tasks bar" onClick={onToggle}>
          <ChevronRight />
        </Button>
        <BarHeading vertical>Tasks</BarHeading>
      </nav>
    )
  }

  return (
    <nav aria-label="Tasks" className="flex w-56 shrink-0 flex-col gap-2 border-r p-4">
      <div className="flex items-center gap-1">
        <div className="grow">
          <BarHeading>Tasks</BarHeading>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label="Collapse tasks bar" onClick={onToggle}>
          <ChevronLeft />
        </Button>
      </div>

      {project && (
        <SortableList
          ids={project.tasks.map((task) => task.slug)}
          onReorder={onReorder}
          className="flex min-h-0 flex-col gap-1 overflow-y-auto"
        >
          {project.tasks.map((task) => (
            <SortableItem key={task.slug} id={task.slug}>
              {(handle, isDragging) => (
                <TaskItem
                  task={task}
                  project={project}
                  highlighted={task.slug === project.focused_task}
                  handle={handle}
                  isDragging={isDragging}
                  onClick={() => onFocus(task.slug)}
                  onCloneAgain={() => onCloneAgain(task.slug)}
                  onDelete={onDelete}
                >
                  <Badge variant="secondary">{task.status}</Badge>
                </TaskItem>
              )}
            </SortableItem>
          ))}
        </SortableList>
      )}
    </nav>
  )
}
